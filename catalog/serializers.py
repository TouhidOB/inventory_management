from rest_framework import serializers

from .models import Brand, Category, Product, ProductVariation, Unit


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "parent", "is_active"]


class BrandSerializer(serializers.ModelSerializer):
    class Meta:
        model = Brand
        fields = ["id", "name", "is_active"]


class UnitSerializer(serializers.ModelSerializer):
    class Meta:
        model = Unit
        fields = ["id", "name", "short_code"]


class ProductVariationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductVariation
        fields = [
            "id", "product", "name", "attributes", "sku", "barcode",
            "cost_price", "selling_price", "current_stock", "is_active",
        ]
        read_only_fields = ["current_stock"]


class ProductSerializer(serializers.ModelSerializer):
    variations = ProductVariationSerializer(many=True, read_only=True)
    is_low_stock = serializers.BooleanField(read_only=True)

    class Meta:
        model = Product
        fields = [
            "id", "name", "sku", "barcode", "category", "brand", "unit",
            "description", "cost_price", "selling_price", "tax_percent",
            "track_inventory", "reorder_level", "current_stock",
            "is_low_stock", "is_active", "variations",
        ]
        read_only_fields = ["current_stock", "is_low_stock"]
