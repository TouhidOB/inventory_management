from rest_framework import serializers

from catalog.models import Product, ProductVariation
from crm.models import Customer

from .models import Payment, Sale, SaleItem, SaleReturn


class SaleItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = SaleItem
        fields = [
            "id", "product", "product_name", "variation", "quantity",
            "unit_price", "unit_cost", "discount", "subtotal",
        ]
        read_only_fields = ["unit_cost", "subtotal"]


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ["id", "amount", "method", "paid_at", "note"]


class SaleSerializer(serializers.ModelSerializer):
    items = SaleItemSerializer(many=True, read_only=True)
    payments = PaymentSerializer(many=True, read_only=True)
    due = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True, default=None)

    class Meta:
        model = Sale
        fields = [
            "id", "invoice_no", "customer", "customer_name", "branch", "sale_date",
            "subtotal", "discount", "tax", "total", "paid", "due", "status",
            "note", "items", "payments", "created_at",
        ]
        read_only_fields = fields


# ---- Input serializers for the checkout service -----------------------------

class SaleItemInputSerializer(serializers.Serializer):
    # Pass the MANAGER (not .all()) so tenant scoping resolves per-request,
    # not at import time when no tenant is in context.
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects)
    variation = serializers.PrimaryKeyRelatedField(
        queryset=ProductVariation.objects, required=False, allow_null=True
    )
    quantity = serializers.DecimalField(max_digits=14, decimal_places=2)
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    discount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=0)


class PaymentInputSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    method = serializers.ChoiceField(choices=Payment.Method.choices, default=Payment.Method.CASH)


class SaleCreateSerializer(serializers.Serializer):
    customer = serializers.PrimaryKeyRelatedField(
        queryset=Customer.objects, required=False, allow_null=True
    )
    discount = serializers.DecimalField(max_digits=14, decimal_places=2, required=False, default=0)
    tax = serializers.DecimalField(max_digits=14, decimal_places=2, required=False, default=0)
    note = serializers.CharField(required=False, allow_blank=True, default="")
    items = SaleItemInputSerializer(many=True)
    payments = PaymentInputSerializer(many=True, required=False, default=list)

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError("A sale needs at least one item.")
        return value


class ReturnLineInputSerializer(serializers.Serializer):
    sale_item = serializers.PrimaryKeyRelatedField(queryset=SaleItem.objects)
    quantity = serializers.DecimalField(max_digits=14, decimal_places=2)


class SaleReturnInputSerializer(serializers.Serializer):
    lines = ReturnLineInputSerializer(many=True)
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    refund_method = serializers.ChoiceField(
        choices=SaleReturn.RefundMethod.choices, default=SaleReturn.RefundMethod.CASH
    )
    refund_reference = serializers.CharField(required=False, allow_blank=True, default="")
    restock = serializers.BooleanField(default=True)
    exchange_items = SaleItemInputSerializer(many=True, required=False, default=list)

    def validate_lines(self, value):
        if not value:
            raise serializers.ValidationError("At least one return line is required.")
        return value


class SaleReturnSerializer(serializers.ModelSerializer):
    class Meta:
        model = SaleReturn
        fields = [
            "id", "sale", "reason", "total_refund", "refund_method",
            "refund_reference", "exchange_sale", "created_at",
        ]
