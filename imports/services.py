"""
Orchestration for the import pipeline: stage → validate → preview, then commit
(transactional upsert) and rollback.

Nothing here writes to live tables until ``commit_job``. Staging fully populates
``ImportRow`` so the preview is exactly what commit will do.
"""
import csv

from django.db import transaction
from django.utils import timezone

from audit.models import AuditLog
from audit.services import record

from .importers import get_importer
from .models import ImportJob, ImportRow


def detect_and_store_columns(job):
    """Read the uploaded file's header row into ``job.detected_columns``."""
    importer = get_importer(job.import_type)
    with job.source_file.open("rb") as fh:
        cols = importer.detect_columns(fh, job.original_filename)
    job.detected_columns = cols
    job.status = ImportJob.Status.UPLOADED
    job.save(update_fields=["detected_columns", "status"])
    return cols


def missing_required_mappings(job):
    """Required platform fields left unmapped — blocks preview (Phase 3 rule)."""
    importer = get_importer(job.import_type)
    mapping = job.column_mapping or {}
    return [
        f.label for f in importer.TARGET_FIELDS
        if f.required and (mapping.get(f.name) in (None, ""))
    ]


@transaction.atomic
def stage_job(job):
    """Extract mapped columns → normalize → validate → match for every row.
    Rebuilds staging from scratch each call so re-mapping is safe."""
    importer = get_importer(job.import_type)
    job.rows.all().delete()

    with job.source_file.open("rb") as fh:
        headers, rows = importer.read_table(fh, job.original_filename)

    header_labels = [c["header"] for c in (job.detected_columns or [])] or headers
    shop = job.shop
    mapping = job.column_mapping or {}

    seen = {}  # match_value -> first row_number
    total = valid = errors = 0
    staged = []

    for i, row in enumerate(rows, start=1):
        raw = {}
        for idx, cell in enumerate(row):
            label = header_labels[idx] if idx < len(header_labels) else f"Column {idx + 1}"
            raw[label] = cell

        mapped = importer.extract_mapped(row, mapping)
        cleaned = importer.normalize_row(mapped)
        status, msgs = importer.validate_row(cleaned)

        # Within-file duplicate handling.
        mv = importer.match_value(cleaned)
        if mv and mv in seen and status != "error":
            if importer.duplicate_in_file == "error":
                status = "error"
                msgs = list(msgs) + [f"Duplicate of row {seen[mv]} in this file"]
            else:
                msgs = list(msgs) + [f"Duplicate of row {seen[mv]} — later value overwrites"]
        elif mv:
            seen.setdefault(mv, i)

        existing = None if status == "error" else importer.match(cleaned, shop)

        if status == "error":
            row_status = ImportRow.Status.ERROR
            errors += 1
        else:
            row_status = ImportRow.Status.WILL_UPDATE if existing else ImportRow.Status.WILL_CREATE
            valid += 1

        staged.append(ImportRow(
            job=job, row_number=i, raw_data=raw,
            cleaned_data=importer.serialize(cleaned),
            status=row_status, match_key=mv,
            matched_object_id=str(existing.pk) if existing else "",
            errors=list(msgs),
        ))
        total += 1

    ImportRow.objects.bulk_create(staged)
    job.total_rows, job.valid_rows, job.error_rows = total, valid, errors
    job.status = ImportJob.Status.PREVIEW_READY
    job.save(update_fields=["total_rows", "valid_rows", "error_rows", "status"])
    return job


def commit_job(job, *, skip_errors=False, actor=None):
    """Transactionally upsert every non-error row. Any unexpected exception rolls
    back the WHOLE job and leaves live data untouched."""
    importer = get_importer(job.import_type)
    shop = job.shop
    created = updated = 0

    job.status = ImportJob.Status.COMMITTING
    job.save(update_fields=["status"])
    try:
        with transaction.atomic():
            qs = job.rows.exclude(status=ImportRow.Status.ERROR).order_by("row_number")
            for row in qs.select_for_update():
                cleaned = importer.hydrate(row.cleaned_data)
                existing = importer.match(cleaned, shop)
                prev = importer.snapshot_previous(existing)
                action, obj = importer.upsert(cleaned, existing, shop, job)
                if action == "created":
                    created += 1
                else:
                    updated += 1
                # Persist rollback info + resolved id on the staged row.
                row.cleaned_data["_previous"] = prev
                row.matched_object_id = str(obj.pk)
                row.status = ImportRow.Status.COMMITTED
                row.save(update_fields=["cleaned_data", "matched_object_id", "status"])

            job.created_count = created
            job.updated_count = updated
            job.committed_at = timezone.now()
            job.status = ImportJob.Status.COMMITTED
            job.error_summary = ""
            job.save(update_fields=[
                "created_count", "updated_count", "committed_at", "status", "error_summary",
            ])
    except Exception as exc:  # noqa: BLE001 — must catch all to guarantee rollback
        job.status = ImportJob.Status.FAILED
        job.error_summary = f"{type(exc).__name__}: {exc}"
        job.save(update_fields=["status", "error_summary"])
        raise

    record(
        action=AuditLog.Action.CREATE, actor=actor, shop=shop, target=job,
        description=(f"Import {job.get_import_type_display()} committed: "
                     f"{created} created, {updated} updated"),
        metadata={"job_id": str(job.id), "created": created, "updated": updated},
    )
    return job


@transaction.atomic
def rollback_job(job, *, actor=None):
    """Reverse a committed job: delete created rows, restore updated rows from
    the ``_previous`` snapshot captured at commit."""
    if job.status != ImportJob.Status.COMMITTED:
        raise ValueError("Only a committed job can be rolled back.")
    importer = get_importer(job.import_type)
    model = importer.get_model()
    reverted = 0
    skipped = []
    for row in job.rows.filter(status=ImportRow.Status.COMMITTED):
        if not row.matched_object_id:
            continue
        obj = model.all_objects.filter(pk=row.matched_object_id).first()
        if obj is None:
            continue
        prev = (row.cleaned_data or {}).get("_previous")
        try:
            if prev is None:  # was created by this job → delete it
                obj.delete()
            else:             # was updated → restore prior field values
                importer.restore_snapshot(obj, prev)
            reverted += 1
        except Exception as exc:  # noqa: BLE001
            skipped.append(f"row {row.row_number}: {type(exc).__name__}")
    job.status = ImportJob.Status.ROLLED_BACK
    job.notes = (job.notes + f"\nRolled back {reverted} row(s). "
                 + (f"Skipped: {'; '.join(skipped)}" if skipped else "")).strip()
    job.save(update_fields=["status", "notes"])
    record(
        action=AuditLog.Action.DELETE, actor=actor, shop=job.shop, target=job,
        description=f"Import {job.id} rolled back: {reverted} row(s) reverted",
    )
    return reverted, skipped


def build_report_csv(job):
    """Row-level result report: every row + final status + errors."""
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["row", "status", "match_key", "matched_id", "messages", "cleaned"])
    for row in job.rows.all().order_by("row_number"):
        cleaned = {k: v for k, v in (row.cleaned_data or {}).items() if k != "_previous"}
        w.writerow([
            row.row_number, row.status, row.match_key, row.matched_object_id,
            " | ".join(row.errors or []), cleaned,
        ])
    return buf.getvalue()
