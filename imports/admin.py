from django.contrib import admin

from .models import ImportJob, ImportMapping, ImportRow


@admin.register(ImportJob)
class ImportJobAdmin(admin.ModelAdmin):
    list_display = ("id", "shop", "import_type", "status", "total_rows",
                    "created_count", "updated_count", "created_at")
    list_filter = ("import_type", "status")
    readonly_fields = ("id", "created_at", "committed_at")


@admin.register(ImportMapping)
class ImportMappingAdmin(admin.ModelAdmin):
    list_display = ("name", "import_type", "shop", "created_at")
    list_filter = ("import_type",)


@admin.register(ImportRow)
class ImportRowAdmin(admin.ModelAdmin):
    list_display = ("job", "row_number", "status", "match_key", "matched_object_id")
    list_filter = ("status",)
