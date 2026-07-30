from django.contrib import admin

from .models import Payment, Sale, SaleItem, SaleReturn, SaleReturnItem


class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 0


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ("invoice_no", "shop", "customer", "total", "paid", "status", "sale_date")
    list_filter = ("status",)
    search_fields = ("invoice_no", "customer__name")
    inlines = [SaleItemInline, PaymentInline]


@admin.register(SaleReturn)
class SaleReturnAdmin(admin.ModelAdmin):
    list_display = ("shop", "sale", "total_refund", "created_at")


admin.site.register(SaleReturnItem)
