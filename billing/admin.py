from django.contrib import admin

from .models import ManualPayment, SubscriptionInvoice


@admin.register(SubscriptionInvoice)
class SubscriptionInvoiceAdmin(admin.ModelAdmin):
    list_display = ("number", "shop", "plan", "amount", "status", "period_end")
    list_filter = ("status", "cycle")
    search_fields = ("number", "shop__name")


@admin.register(ManualPayment)
class ManualPaymentAdmin(admin.ModelAdmin):
    list_display = ("id", "shop", "amount", "method", "status", "submitted_at", "reviewed_at")
    list_filter = ("status", "method")
    search_fields = ("shop__name", "payer_reference")
    readonly_fields = ("submitted_at",)
