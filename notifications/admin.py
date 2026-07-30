from django.contrib import admin

from .models import Notification, ShopAlertConfig, ShopWhatsAppConfig


@admin.register(ShopWhatsAppConfig)
class ShopWhatsAppConfigAdmin(admin.ModelAdmin):
    list_display = ("shop", "enabled", "use_platform_number", "phone_number_id")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("created_at", "shop", "type", "title", "is_read")
    list_filter = ("type", "is_read")
    search_fields = ("title", "message")


@admin.register(ShopAlertConfig)
class ShopAlertConfigAdmin(admin.ModelAdmin):
    list_display = ("shop", "low_stock_enabled", "mode", "email_enabled")
