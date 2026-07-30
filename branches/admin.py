from django.contrib import admin

from .models import StockTransfer, StockTransferItem


class TransferItemInline(admin.TabularInline):
    model = StockTransferItem
    extra = 0


@admin.register(StockTransfer)
class StockTransferAdmin(admin.ModelAdmin):
    list_display = ("id", "shop", "source_branch", "dest_branch", "status", "received_at")
    list_filter = ("status",)
    inlines = [TransferItemInline]
