from rest_framework import serializers

from catalog.models import Product, ProductVariation

from .models import MovementType, StockMovement


class StockMovementSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = StockMovement
        fields = [
            "id", "product", "product_name", "variation", "branch",
            "movement_type", "quantity", "unit_cost", "reference_type",
            "reference_id", "note", "created_at",
        ]


class StockAdjustmentSerializer(serializers.Serializer):
    """Manual stock adjustment (in/out/damage/opening) via the ledger."""

    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects)
    variation = serializers.PrimaryKeyRelatedField(
        queryset=ProductVariation.objects, required=False, allow_null=True
    )
    movement_type = serializers.ChoiceField(choices=[
        MovementType.ADJUST_IN, MovementType.ADJUST_OUT,
        MovementType.DAMAGE_OUT, MovementType.OPENING,
    ])
    quantity = serializers.DecimalField(max_digits=14, decimal_places=2)
    unit_cost = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=0)
    note = serializers.CharField(required=False, allow_blank=True, default="")
