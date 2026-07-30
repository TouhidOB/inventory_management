from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from audit.models import AuditLog
from audit.services import record
from core.api import TenantScopedViewSet
from core.permissions import HasPermCode, IsTenantMember
from core.tenant_context import set_current_tenant

from .models import StockMovement
from .serializers import StockAdjustmentSerializer, StockMovementSerializer
from .services import apply_movement


class StockMovementViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """Read-only view of the append-only ledger + a manual-adjust action."""

    permission_classes = [IsTenantMember, HasPermCode]
    required_perm = "view_inventory"
    serializer_class = StockMovementSerializer

    def initial(self, request, *args, **kwargs):
        set_current_tenant(getattr(request.user, "shop", None))
        request.tenant = getattr(request.user, "shop", None)
        super().initial(request, *args, **kwargs)

    def get_queryset(self):
        qs = StockMovement.objects.select_related("product")
        if product_id := self.request.query_params.get("product"):
            qs = qs.filter(product_id=product_id)
        return qs

    @action(detail=False, methods=["post"])
    def adjust(self, request):
        """Manual stock adjustment. Requires the manage_inventory permission."""
        if not request.user.has_perm_code("manage_inventory"):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        ser = StockAdjustmentSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        movement = apply_movement(
            shop=request.user.shop,
            product=data["product"],
            variation=data.get("variation"),
            movement_type=data["movement_type"],
            quantity=data["quantity"],
            unit_cost=data.get("unit_cost", 0),
            note=data.get("note", ""),
            created_by=request.user,
        )
        record(
            action=AuditLog.Action.STOCK_ADJUST, actor=request.user,
            shop=request.user.shop, target=movement,
            description=f"{data['movement_type']} {data['quantity']} of {data['product'].name}",
        )
        return Response(StockMovementSerializer(movement).data, status=status.HTTP_201_CREATED)
