from django.contrib import admin

from .models import APIKey


@admin.register(APIKey)
class APIKeyAdmin(admin.ModelAdmin):
    list_display = ("name", "shop", "prefix", "can_read", "can_write", "rate_tier", "is_active", "last_used_at")
    list_filter = ("is_active", "rate_tier", "can_write")
    search_fields = ("name", "prefix", "shop__name")
    readonly_fields = ("key_hash", "prefix")
