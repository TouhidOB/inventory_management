from rest_framework import serializers

from .models import Customer


class CustomerSerializer(serializers.ModelSerializer):
    days_since_last_purchase = serializers.IntegerField(read_only=True)

    class Meta:
        model = Customer
        fields = [
            "id", "name", "phone", "email", "address", "segment",
            "due_balance", "total_purchased", "last_purchase_at",
            "days_since_last_purchase", "is_active",
        ]
        read_only_fields = ["due_balance", "total_purchased", "last_purchase_at"]
