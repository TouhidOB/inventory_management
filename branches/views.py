"""Branch management API — gated behind the `multi_branch` plan feature."""
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.models import Product, ProductVariation
from core.api import TenantScopedViewSet
from core.gating import FeatureRequired
from core.permissions import HasPermCode, IsTenantMember
from core.tenant_context import set_current_tenant
from tenants.models import Branch

from .models import StockTransfer
from .services import branch_comparison, create_transfer, receive_transfer

MultiBranchRequired = FeatureRequired.as_permission("multi_branch")


class BranchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = ["id", "name", "code", "phone", "address", "is_main", "is_active"]


class BranchViewSet(TenantScopedViewSet):
    """Branch CRUD. Branch is not TenantScopedModel, so scope explicitly."""

    serializer_class = BranchSerializer
    permission_classes = [IsTenantMember, HasPermCode, MultiBranchRequired]
    required_perm = "manage_settings"
    feature_flag = "multi_branch"

    def get_queryset(self):
        return Branch.objects.filter(shop_id=self.request.user.shop_id)

    def perform_create(self, serializer):
        serializer.save(shop_id=self.request.user.shop_id)


class TransferItemInput(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects)
    variation = serializers.PrimaryKeyRelatedField(queryset=ProductVariation.objects, required=False, allow_null=True)
    quantity = serializers.DecimalField(max_digits=14, decimal_places=2)
    unit_cost = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=0)


class TransferCreateSerializer(serializers.Serializer):
    source_branch = serializers.PrimaryKeyRelatedField(queryset=Branch.objects)
    dest_branch = serializers.PrimaryKeyRelatedField(queryset=Branch.objects)
    note = serializers.CharField(required=False, allow_blank=True, default="")
    items = TransferItemInput(many=True)


class TransferSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockTransfer
        fields = ["id", "source_branch", "dest_branch", "status", "note", "received_at", "created_at"]


class StockTransferViewSet(TenantScopedViewSet):
    permission_classes = [IsTenantMember, HasPermCode, MultiBranchRequired]
    required_perm = "manage_inventory"
    feature_flag = "multi_branch"

    def get_queryset(self):
        return StockTransfer.objects.prefetch_related("items")

    def get_serializer_class(self):
        return TransferCreateSerializer if self.action == "create" else TransferSerializer

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        try:
            transfer = create_transfer(
                shop=request.user.shop, source_branch=d["source_branch"],
                dest_branch=d["dest_branch"], items=d["items"],
                note=d.get("note", ""), created_by=request.user,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(TransferSerializer(transfer).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def receive(self, request, pk=None):
        transfer = self.get_object()
        try:
            transfer = receive_transfer(transfer=transfer, created_by=request.user)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(TransferSerializer(transfer).data)


class BranchComparisonView(APIView):
    permission_classes = [IsTenantMember, HasPermCode, MultiBranchRequired]
    required_perm = "view_reports"
    feature_flag = "multi_branch"

    def initial(self, request, *args, **kwargs):
        set_current_tenant(getattr(request.user, "shop", None))
        request.tenant = getattr(request.user, "shop", None)
        super().initial(request, *args, **kwargs)

    def get(self, request):
        return Response(branch_comparison(request.user.shop))
