from django.contrib import admin

from .models import Brand, Category, Product, ProductVariation, Unit


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "shop", "parent", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("name", "shop", "is_active")
    search_fields = ("name",)


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ("name", "short_code", "shop")


class VariationInline(admin.TabularInline):
    model = ProductVariation
    extra = 0


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "shop", "sku", "cost_price", "selling_price", "current_stock", "is_active")
    list_filter = ("is_active", "track_inventory")
    search_fields = ("name", "sku", "barcode")
    inlines = [VariationInline]
