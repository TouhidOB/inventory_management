from django.contrib import admin

from .models import Customer


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "shop", "phone", "segment", "due_balance", "last_purchase_at")
    list_filter = ("segment", "is_active")
    search_fields = ("name", "phone", "email")
