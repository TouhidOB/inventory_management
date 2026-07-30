from decimal import Decimal

from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from core.permissions import HasPermCode, IsTenantMember
from core.tenant_context import set_current_tenant

from .models import Sale
from .returns import create_return
from .serializers import (
    SaleCreateSerializer,
    SaleReturnInputSerializer,
    SaleReturnSerializer,
    SaleSerializer,
)
from .services import add_payment, cancel_sale, create_sale


class SaleViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin,
    mixins.CreateModelMixin, viewsets.GenericViewSet,
):
    permission_classes = [IsTenantMember, HasPermCode]
    required_perm = "create_sale"

    def initial(self, request, *args, **kwargs):
        set_current_tenant(getattr(request.user, "shop", None))
        request.tenant = getattr(request.user, "shop", None)
        super().initial(request, *args, **kwargs)

    def get_queryset(self):
        # items__product: the serializer reads product.name per line, so prefetch
        # the products too or it's an N+1 (one product query per sale line).
        qs = Sale.objects.select_related("customer").prefetch_related(
            "items__product", "payments"
        )
        if s := self.request.query_params.get("status"):
            qs = qs.filter(status=s)
        if self.request.query_params.get("with_due") in {"1", "true"}:
            qs = qs.exclude(status__in=[Sale.Status.PAID, Sale.Status.CANCELLED])
        return qs

    def get_serializer_class(self):
        return SaleCreateSerializer if self.action == "create" else SaleSerializer

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
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

    @action(detail=True, methods=["post"])
    def add_payment(self, request, pk=None):
        sale = self.get_object()
        try:
            sale = add_payment(
                sale=sale, amount=Decimal(request.data.get("amount", 0)),
                method=request.data.get("method", "cash"), created_by=request.user,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(SaleSerializer(sale).data)

    @action(detail=True, methods=["post"], url_path="return")
    def return_items(self, request, pk=None):
        """Full/partial return, optionally with exchange items (8.4)."""
        if not request.user.has_perm_code("process_return"):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        sale = self.get_object()
        ser = SaleReturnInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        lines = [{"sale_item": l["sale_item"], "quantity": l["quantity"]} for l in data["lines"]]
        try:
            sret, exchange, net = create_return(
                sale=sale, lines=lines, reason=data["reason"],
                refund_method=data["refund_method"],
                refund_reference=data["refund_reference"],
                restock=data["restock"],
                exchange_items=data.get("exchange_items") or None,
                created_by=request.user,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({
            "sale_return": SaleReturnSerializer(sret).data,
            "exchange_sale": SaleSerializer(exchange).data if exchange else None,
            "net_amount": net,
            "sale_status": sale.status,
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        if not request.user.has_perm_code("delete_sale"):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        sale = self.get_object()
        try:
            sale = cancel_sale(sale=sale, created_by=request.user)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(SaleSerializer(sale).data)
