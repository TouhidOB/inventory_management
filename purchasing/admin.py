from django.contrib import admin

from .models import PurchaseOrder, PurchaseOrderItem, Supplier


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name", "shop", "phone", "due_balance", "is_active")
    search_fields = ("name", "phone")


class POItemInline(admin.TabularInline):
    model = PurchaseOrderItem
    extra = 0


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ("po_number", "shop", "supplier", "status", "total", "paid", "received_at")
    list_filter = ("status",)
    search_fields = ("po_number", "supplier__name")
    inlines = [POItemInline]
