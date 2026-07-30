from datetime import timedelta

from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.response import Response

from core.api import TenantScopedViewSet

from .models import Customer
from .serializers import CustomerSerializer


class CustomerViewSet(TenantScopedViewSet):
    serializer_class = CustomerSerializer
    required_perm = "manage_customers"

    def get_queryset(self):
        qs = Customer.objects.all()
        params = self.request.query_params
        if segment := params.get("segment"):
            qs = qs.filter(segment=segment)
        if params.get("with_due") in {"1", "true"}:
            qs = qs.filter(due_balance__gt=0)
        if search := params.get("search"):
            from django.db.models import Q
            qs = qs.filter(Q(name__icontains=search) | Q(phone__icontains=search))
        return qs

    @action(detail=False, methods=["get"])
    def inactive(self, request):
        """Customers with no purchase in N (default 60) days."""
        days = int(request.query_params.get("days", 60))
        cutoff = timezone.now() - timedelta(days=days)
        qs = self.get_queryset().filter(last_purchase_at__lt=cutoff)
        return Response(self.get_serializer(qs, many=True).data)
