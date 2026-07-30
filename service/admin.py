from django.contrib import admin

from .models import (
    ServiceTicket,
    ServiceTicketPart,
    ServiceTicketStatusHistory,
    Warranty,
    WarrantyClaim,
)


@admin.register(Warranty)
class WarrantyAdmin(admin.ModelAdmin):
    list_display = ("id", "shop", "product", "customer", "expiry_date", "status")
    list_filter = ("status",)
    search_fields = ("serial_no", "product__name")


@admin.register(WarrantyClaim)
class WarrantyClaimAdmin(admin.ModelAdmin):
    list_display = ("id", "shop", "warranty", "status", "claim_date")
    list_filter = ("status",)


class PartInline(admin.TabularInline):
    model = ServiceTicketPart
    extra = 0


class HistoryInline(admin.TabularInline):
    model = ServiceTicketStatusHistory
    extra = 0


@admin.register(ServiceTicket)
class ServiceTicketAdmin(admin.ModelAdmin):
    list_display = ("ticket_no", "shop", "customer", "status", "technician", "estimated_delivery")
    list_filter = ("status",)
    search_fields = ("ticket_no", "device_description")
    inlines = [PartInline, HistoryInline]
