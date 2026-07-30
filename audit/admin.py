from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "actor", "shop", "target_model", "target_id")
    list_filter = ("action", "created_at")
    search_fields = ("actor__email", "shop__name", "target_model", "target_id", "description")
    readonly_fields = [f.name for f in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False  # audit log is append-only, written by services

    def has_change_permission(self, request, obj=None):
        return False
