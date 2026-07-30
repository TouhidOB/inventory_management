"""
POS endpoints — kept deliberately thin and fast. Barcode lookup + a checkout
that wraps the sales service. Speed target: <30s per transaction, so minimal
round-trips (one lookup per scan, one checkout call).
"""
from django.db.models import Q
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.models import Product
from catalog.serializers import ProductSerializer
from core.permissions import HasPermCode, IsTenantMember
from core.tenant_context import set_current_tenant
from sales.serializers import SaleCreateSerializer, SaleSerializer
from sales.services import create_sale


class _POSBase(APIView):
    permission_classes = [IsTenantMember, HasPermCode]
    required_perm = "create_sale"

    def initial(self, request, *args, **kwargs):
        set_current_tenant(getattr(request.user, "shop", None))
        request.tenant = getattr(request.user, "shop", None)
        super().initial(request, *args, **kwargs)


class BarcodeLookupView(_POSBase):
    def get(self, request):
        code = request.query_params.get("barcode") or request.query_params.get("q")
        if not code:
            return Response({"detail": "Provide ?barcode="}, status=status.HTTP_400_BAD_REQUEST)
        product = (
            Product.objects.filter(is_active=True)
            .filter(Q(barcode=code) | Q(sku=code))
            .select_related("category", "brand", "unit")
            .first()
        )
        if product is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(ProductSerializer(product).data)


class CheckoutView(_POSBase):
    def post(self, request):
        ser = SaleCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        sale = create_sale(
            shop=request.user.shop,
            customer=data.get("customer"),
            discount=data.get("discount", 0),
            tax=data.get("tax", 0),
            note=data.get("note", ""),
            items=data["items"],
            payments=data.get("payments", []),
            created_by=request.user,
        )
        return Response(SaleSerializer(sale).data, status=status.HTTP_201_CREATED)
