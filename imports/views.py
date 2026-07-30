"""
Server-rendered, step-based import wizard (super-admin only):
    upload → map columns → validate/preview → confirm/commit → report/rollback.

Each step is a normal POST→redirect so it works without JS; preview filters and
step swaps are HTMX-friendly partials. The target shop is ALWAYS an explicit form
field — never inferred from the request tenant, since the super admin is cross-tenant.
"""
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from core.tenant_context import bypass_tenant_scope
from tenants.models import Shop

from .importers import get_importer
from .models import ImportJob, ImportMapping, ImportRow
from .permissions import platform_superadmin_required
from .services import (
    build_report_csv,
    commit_job,
    detect_and_store_columns,
    missing_required_mappings,
    rollback_job,
    stage_job,
)


def _shops():
    with bypass_tenant_scope():
        return list(Shop.objects.order_by("name"))


@platform_superadmin_required
def index(request):
    jobs = ImportJob.objects.select_related("shop").all()[:50]
    return render(request, "imports/index.html", {
        "active": "imports", "jobs": jobs, "shops": _shops(),
        "types": ImportJob.Type.choices,
    })


@platform_superadmin_required
@require_http_methods(["POST"])
def upload(request):
    shop_id = request.POST.get("shop")
    import_type = request.POST.get("import_type")
    f = request.FILES.get("file")
    if not (shop_id and import_type in dict(ImportJob.Type.choices) and f):
        messages.error(request, "Pick a shop, an import type and a file.")
        return redirect("imports:index")
    if f.size > 10 * 1024 * 1024:  # 10 MB cap — guard against huge-file uploads
        messages.error(request, "File is too large (max 10 MB).")
        return redirect("imports:index")
    if not f.name.lower().endswith((".csv", ".xlsx")):
        messages.error(request, "Upload a CSV or .xlsx file.")
        return redirect("imports:index")
    with bypass_tenant_scope():
        shop = get_object_or_404(Shop.objects, pk=shop_id)
    job = ImportJob.objects.create(
        shop=shop, import_type=import_type, source_file=f,
        original_filename=f.name, created_by=request.user,
    )
    detect_and_store_columns(job)
    return redirect("imports:mapping", job_id=job.id)


def _mapping_context(job):
    importer = get_importer(job.import_type)
    suggested = importer.suggest_mapping(job.detected_columns)
    suggested.update({k: v for k, v in (job.column_mapping or {}).items()})
    templates = ImportMapping.objects.filter(import_type=job.import_type).filter(
        shop__isnull=True
    ) | ImportMapping.objects.filter(import_type=job.import_type, shop=job.shop)
    return {
        "active": "imports", "job": job, "fields": importer.TARGET_FIELDS,
        "columns": job.detected_columns, "mapping": suggested,
        "templates": templates.distinct(),
    }


@platform_superadmin_required
def mapping(request, job_id):
    job = get_object_or_404(ImportJob, pk=job_id)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "load":
            try:
                tpl_pk = int(request.POST.get("template"))
            except (TypeError, ValueError):
                tpl_pk = None
            tpl = ImportMapping.objects.filter(pk=tpl_pk).first()
            if tpl:
                job.column_mapping = tpl.mapping
                job.save(update_fields=["column_mapping"])
                messages.success(request, f"Loaded template '{tpl.name}'.")
            return redirect("imports:mapping", job_id=job.id)

        importer = get_importer(job.import_type)
        chosen = {}
        for f in importer.TARGET_FIELDS:
            val = request.POST.get(f"map_{f.name}", "")
            if val != "":
                chosen[f.name] = int(val)
        job.column_mapping = chosen
        job.status = ImportJob.Status.MAPPING
        job.save(update_fields=["column_mapping", "status"])

        missing = missing_required_mappings(job)
        if missing:
            messages.error(request, "Map all required fields: " + ", ".join(missing))
            return render(request, "imports/mapping.html", _mapping_context(job))

        stage_job(job)

        if request.POST.get("save_template") and request.POST.get("template_name"):
            ImportMapping.objects.create(
                shop=job.shop, import_type=job.import_type,
                name=request.POST["template_name"], mapping=chosen,
                created_by=request.user,
            )
            messages.success(request, "Mapping saved as a template.")
        return redirect("imports:preview", job_id=job.id)

    return render(request, "imports/mapping.html", _mapping_context(job))


@platform_superadmin_required
def preview(request, job_id):
    job = get_object_or_404(ImportJob, pk=job_id)
    only = request.GET.get("only")
    rows = job.rows.all()
    if only == "errors":
        rows = rows.filter(status=ImportRow.Status.ERROR)
    elif only == "warnings":
        rows = rows.exclude(status=ImportRow.Status.ERROR).exclude(errors=[])
    page = Paginator(rows, 50).get_page(request.GET.get("page"))
    importer = get_importer(job.import_type)
    field_map = [
        (f.label, next((c["header"] for c in job.detected_columns
                        if c["index"] == job.column_mapping.get(f.name)), "— skip —"))
        for f in importer.TARGET_FIELDS
    ]
    ctx = {"active": "imports", "job": job, "page_obj": page, "rows": page,
           "only": only, "field_map": field_map}
    template = "imports/_preview_rows.html" if request.headers.get("HX-Request") else "imports/preview.html"
    return render(request, template, ctx)


@platform_superadmin_required
@require_http_methods(["POST"])
def commit(request, job_id):
    job = get_object_or_404(ImportJob, pk=job_id)
    if not job.is_committable:
        messages.error(request, "This job is not ready to commit.")
        return redirect("imports:preview", job_id=job.id)
    skip_errors = request.POST.get("skip_errors") == "on"
    if job.error_rows > 0 and not skip_errors:
        messages.error(request, "There are error rows. Fix them or tick 'skip error rows'.")
        return redirect("imports:preview", job_id=job.id)
    try:
        commit_job(job, skip_errors=skip_errors, actor=request.user)
        messages.success(request, f"Import committed: {job.created_count} created, "
                                  f"{job.updated_count} updated.")
    except Exception as exc:  # noqa: BLE001
        messages.error(request, f"Import failed and was rolled back: {exc}")
    return redirect("imports:detail", job_id=job.id)


@platform_superadmin_required
def detail(request, job_id):
    job = get_object_or_404(ImportJob.objects.select_related("shop"), pk=job_id)
    return render(request, "imports/detail.html", {"active": "imports", "job": job})


@platform_superadmin_required
def report(request, job_id):
    job = get_object_or_404(ImportJob, pk=job_id)
    resp = HttpResponse(build_report_csv(job), content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="import_{job.id}_report.csv"'
    return resp


@platform_superadmin_required
@require_http_methods(["POST"])
def rollback(request, job_id):
    job = get_object_or_404(ImportJob, pk=job_id)
    try:
        reverted, skipped = rollback_job(job, actor=request.user)
        msg = f"Rolled back {reverted} row(s)."
        if skipped:
            msg += f" Could not revert {len(skipped)} (still referenced)."
        messages.success(request, msg)
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("imports:detail", job_id=job.id)
