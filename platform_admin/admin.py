from django.contrib import admin

from .models import ContactMessage


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ("created_at", "name", "email", "subject", "is_read")
    list_filter = ("is_read",)
    search_fields = ("name", "email", "subject", "message")
