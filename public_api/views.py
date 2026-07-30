"""
Public API v1 (9.7) — a deliberately curated read/write surface for Enterprise
integrations. NO user management, billing, or platform-admin here. NO payment
actions of any kind. Tenant is bound by the API key in authentication.
"""
from rest_framework import mixins, status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.models import Product
from catalog.serializers import ProductSerializer
from crm.models import Customer
from crm.serializers import CustomerSerializer
from reports.datasets import BUILDERS
from sales.serializers import SaleCreateSerializer, SaleSerializer
from sales.services import create_sale

from .auth import APIKeyAuthentication
from .permissions import APIKeyRateThrottle, HasResourceScope


class _PublicMixin:
    authentication_classes = [APIKeyAuthentication]
    permission_classes = [HasResourceScope]
    throttle_classes = [APIKeyRateThrottle]

    @property
    def shop(self):
        return self.request.auth.shop


class _PublicBase(_PublicMixin, viewsets.GenericViewSet):
    pass


class _PublicAPIView(_PublicMixin, APIView):
    pass


class ProductV1ViewSet(_PublicBase, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                       mixins.CreateModelMixin, mixins.UpdateModelMixin):
    resource = "products"
    serializer_class = ProductSerializer

    def get_queryset(self):
        return Product.objects.all().order_by("id")

    def perform_create(self, serializer):
        serializer.save(shop=self.shop)


class InventoryV1View(_PublicAPIView):
    """Read-only current stock levels."""

    resource = "inventory"

    def get(self, request):
        rows = Product.objects.filter(is_active=True).values(
            "id", "name", "sku", "barcode", "current_stock", "reorder_level"
        ).order_by("id")
        return Response(list(rows))


class CustomerV1ViewSet(_PublicBase, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                        mixins.CreateModelMixin, mixins.UpdateModelMixin):
    resource = "customers"
    serializer_class = CustomerSerializer

    def get_queryset(self):
        return Customer.objects.all().order_by("id")

    def perform_create(self, serializer):
        serializer.save(shop=self.shop)


class SaleV1ViewSet(_PublicBase, mixins.ListModelMixin, mixins.CreateModelMixin):
    """External sale creation (e.g. e-commerce storefront sync)."""

    resource = "sales"

    def get_queryset(self):
        from sales.models import Sale
        return Sale.objects.all().order_by("-id")

    def get_serializer_class(self):
        return SaleCreateSerializer if self.action == "create" else SaleSerializer

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        sale = create_sale(
            shop=self.shop, customer=d.get("customer"),
            discount=d.get("discount", 0), tax=d.get("tax", 0),
            note=d.get("note", ""), items=d["items"], payments=d.get("payments", []),
            created_by=None,
        )
        return Response(SaleSerializer(sale).data, status=status.HTTP_201_CREATED)


class ReportV1View(_PublicAPIView):
    resource = "reports"

    def get(self, request):
        rtype = request.query_params.get("type")
        builder = BUILDERS.get(rtype)
        if builder is None:
            return Response({"detail": f"Unknown report. Options: {sorted(BUILDERS)}"}, status=400)
        title, columns, rows = builder(self.shop)
        return Response({"title": title, "columns": columns, "rows": rows})
