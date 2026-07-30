"""
Staging models for the super-admin bulk import pipeline.

These tables are PLATFORM-level (not tenant-scoped): a platform super admin
operates across shops, so ``shop`` is an explicit FK chosen per job rather than
resolved from the thread-local tenant. Staging (``ImportRow``) never writes to
live tables — the commit step (``imports.services``) reads ``cleaned_data`` and
upserts through each app's scoped manager.
"""
import uuid

from django.db import models


class ImportJob(models.Model):
    class Type(models.TextChoices):
        PRODUCTS = "products", "Products"
        SUPPLIER_DUES = "supplier_dues", "Supplier dues"
        CUSTOMER_DUES = "customer_dues", "Customer dues"

    class Status(models.TextChoices):
        UPLOADED = "uploaded", "Uploaded"
        MAPPING = "mapping", "Mapping"
        VALIDATING = "validating", "Validating"
        PREVIEW_READY = "preview_ready", "Preview ready"
        COMMITTING = "committing", "Committing"
        COMMITTED = "committed", "Committed"
        FAILED = "failed", "Failed"
        ROLLED_BACK = "rolled_back", "Rolled back"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.ForeignKey(
        "tenants.Shop", on_delete=models.CASCADE, related_name="import_jobs"
    )
    import_type = models.CharField(max_length=20, choices=Type.choices)
    source_file = models.FileField(upload_to="imports/%Y/%m/")
    original_filename = models.CharField(max_length=255, blank=True)

    # Source file columns as [{"index": 0, "header": "SKU-CODE"}], filled at upload.
    detected_columns = models.JSONField(default=list, blank=True)
    # Admin's manual choices: {platform_field: source_column_index}. Only fields
    # present here are ever read; every unmapped source column is ignored.
    column_mapping = models.JSONField(default=dict, blank=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UPLOADED)
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="import_jobs",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    committed_at = models.DateTimeField(null=True, blank=True)

    total_rows = models.PositiveIntegerField(default=0)
    valid_rows = models.PositiveIntegerField(default=0)
    error_rows = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)

    notes = models.TextField(blank=True)
    error_summary = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["shop", "import_type"])]

    def __str__(self):
        return f"{self.get_import_type_display()} → {self.shop_id} ({self.status})"

    @property
    def is_committable(self):
        return self.status == self.Status.PREVIEW_READY


class ImportMapping(models.Model):
    """A reusable column-mapping template so repeat imports skip re-mapping.
    ``shop=None`` = a global template available for every shop."""

    shop = models.ForeignKey(
        "tenants.Shop", on_delete=models.CASCADE, null=True, blank=True,
        related_name="import_mappings",
    )
    import_type = models.CharField(max_length=20, choices=ImportJob.Type.choices)
    name = models.CharField(max_length=120)
    mapping = models.JSONField(default=dict)  # same shape as ImportJob.column_mapping
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="import_mappings",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["import_type", "name"]

    def __str__(self):
        return f"{self.name} ({self.import_type})"


class ImportRow(models.Model):
    """One staged source row. Never writes to live tables until commit."""

    class Status(models.TextChoices):
        VALID = "valid", "Valid"
        WARNING = "warning", "Warning"
        ERROR = "error", "Error"
        WILL_CREATE = "will_create", "Will create"
        WILL_UPDATE = "will_update", "Will update"
        COMMITTED = "committed", "Committed"

    job = models.ForeignKey(ImportJob, on_delete=models.CASCADE, related_name="rows")
    row_number = models.PositiveIntegerField()
    # Full source row — kept only for the error report / audit trail.
    raw_data = models.JSONField(default=dict)
    # Only the mapped platform fields after normalization — the sole data that
    # ever reaches live tables.
    cleaned_data = models.JSONField(default=dict)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.VALID)
    match_key = models.CharField(max_length=200, blank=True)
    matched_object_id = models.CharField(max_length=64, blank=True)
    errors = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["row_number"]
        indexes = [models.Index(fields=["job", "status"])]

    def __str__(self):
        return f"row {self.row_number} ({self.status})"

    @property
    def has_error(self):
        return self.status == self.Status.ERROR
