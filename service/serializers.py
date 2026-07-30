from rest_framework import serializers

from catalog.models import Product
from crm.models import Customer

from .models import (
    ServiceTicket,
    ServiceTicketPart,
    ServiceTicketStatusHistory,
    Warranty,
    WarrantyClaim,
)


class WarrantySerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True, default=None)

    class Meta:
        model = Warranty
        fields = [
            "id", "sale_item", "product", "product_name", "customer", "customer_name",
            "serial_no", "period_months", "start_date", "expiry_date", "terms", "status",
        ]
        read_only_fields = ["expiry_date"]


class WarrantyClaimSerializer(serializers.ModelSerializer):
    class Meta:
        model = WarrantyClaim
        fields = ["id", "warranty", "claim_date", "issue_description", "resolution", "resolved_by", "status"]


class TicketPartSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = ServiceTicketPart
        fields = ["id", "product", "product_name", "quantity", "unit_cost", "from_stock"]


class TicketHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceTicketStatusHistory
        fields = ["id", "from_status", "to_status", "note", "changed_by", "created_at"]


class ServiceTicketSerializer(serializers.ModelSerializer):
    parts = TicketPartSerializer(many=True, read_only=True)
    history = TicketHistorySerializer(many=True, read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)

    class Meta:
        model = ServiceTicket
        fields = [
            "id", "ticket_no", "branch", "customer", "device_description", "complaint",
            "received_at", "technician", "status", "service_charge", "estimated_delivery",
            "actual_delivery", "is_overdue", "parts", "history", "created_at",
        ]
        read_only_fields = ["ticket_no", "actual_delivery", "status"]


class TicketCreateSerializer(serializers.Serializer):
    customer = serializers.PrimaryKeyRelatedField(queryset=Customer.objects, required=False, allow_null=True)
    device_description = serializers.CharField(max_length=200)
    complaint = serializers.CharField()
    service_charge = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=0)
    estimated_delivery = serializers.DateField(required=False, allow_null=True)


class TicketPartInputSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects)
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2, default=1)
    unit_cost = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    from_stock = serializers.BooleanField(default=True)
