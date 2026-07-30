from rest_framework import serializers

from catalog.models import Product, ProductVariation

from .models import PurchaseOrder, PurchaseOrderItem, Supplier


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = ["id", "name", "phone", "email", "address", "due_balance", "is_active"]
        read_only_fields = ["due_balance"]


class PurchaseOrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = PurchaseOrderItem
        fields = ["id", "product", "product_name", "variation", "quantity", "unit_cost", "subtotal"]
        read_only_fields = ["subtotal"]


class PurchaseOrderItemInputSerializer(serializers.Serializer):
    # Manager (not .all()) => tenant scoping resolves per-request.
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects)
    variation = serializers.PrimaryKeyRelatedField(
        queryset=ProductVariation.objects, required=False, allow_null=True
    )
    quantity = serializers.DecimalField(max_digits=14, decimal_places=2)
    unit_cost = serializers.DecimalField(max_digits=12, decimal_places=2)


class PurchaseOrderSerializer(serializers.ModelSerializer):
    items = PurchaseOrderItemSerializer(many=True, read_only=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    due = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = [
            "id", "po_number", "supplier", "supplier_name", "branch", "status",
            "order_date", "received_at", "subtotal", "discount", "total",
            "paid", "due", "note", "items", "created_at",
        ]
        read_only_fields = ["po_number", "status", "received_at", "subtotal", "total", "paid"]


class PurchaseOrderCreateSerializer(serializers.Serializer):
    supplier = serializers.PrimaryKeyRelatedField(queryset=Supplier.objects)
    discount = serializers.DecimalField(max_digits=14, decimal_places=2, required=False, default=0)
    note = serializers.CharField(required=False, allow_blank=True, default="")
    items = PurchaseOrderItemInputSerializer(many=True)

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError("At least one item is required.")
        return value
