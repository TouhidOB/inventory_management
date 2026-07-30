from django.contrib import admin

from .models import StockMovement


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("created_at", "shop", "product", "movement_type", "quantity", "unit_cost")
    list_filter = ("movement_type",)
    search_fields = ("product__name", "reference_id")
    readonly_fields = [f.name for f in StockMovement._meta.fields]

    def has_add_permission(self, request):
        return False  # ledger is append-only via services

    def has_change_permission(self, request, obj=None):
        return False
