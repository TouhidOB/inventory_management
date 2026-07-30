"""
Super-admin (platform) API — operated by platform staff, not tenants.

All reads use ``bypass_tenant_scope`` because platform staff legitimately see
across every tenant. Impersonation ("login as shop owner") is session-based and
fully audited on start and stop.
"""
from django.db.models import Count
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import AuditLog
from audit.services import record
from core.middleware import IMPERSONATE_SESSION_KEY
from core.permissions import IsPlatformStaff
from core.tenant_context import bypass_tenant_scope
from tenants.models import Shop


class ShopAdminSerializer(serializers.ModelSerializer):
    plan_tier = serializers.CharField(source="plan.tier", read_only=True, default=None)
    user_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = Shop
        fields = [
            "id", "name", "slug", "business_type", "phone", "email",
            "plan", "plan_tier", "is_active", "trial_ends_at",
            "user_count", "created_at",
        ]
        read_only_fields = ["id", "slug", "created_at"]


class PlatformDashboardView(APIView):
    permission_classes = [IsPlatformStaff]

    def get(self, request):
        now = timezone.now()
        with bypass_tenant_scope():
            shops = Shop.objects.all()
            total = shops.count()
            active = shops.filter(is_active=True).count()
            on_trial = shops.filter(trial_ends_at__gt=now).count()
            by_type = list(
                shops.values("business_type").annotate(n=Count("id")).order_by("-n")
            )
        return Response({
            "total_shops": total,
            "active_shops": active,
            "trial_shops": on_trial,
            "suspended_shops": total - active,
            "by_business_type": by_type,
        })


class ShopAdminViewSet(viewsets.ModelViewSet):
    """CRUD + suspend/activate + impersonate over all shops (staff only)."""

    permission_classes = [IsPlatformStaff]
    serializer_class = ShopAdminSerializer

    def get_queryset(self):
        with bypass_tenant_scope():
            return Shop.objects.select_related("plan").annotate(
                user_count=Count("users")
            ).order_by("-created_at")

    @action(detail=True, methods=["post"])
    def suspend(self, request, pk=None):
        shop = self.get_object()
        shop.is_active = False
        shop.save(update_fields=["is_active"])
        record(action=AuditLog.Action.SUSPEND, actor=request.user, shop=shop,
               target=shop, description="Shop suspended by platform admin")
        return Response({"status": "suspended"})

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        shop = self.get_object()
        shop.is_active = True
        shop.save(update_fields=["is_active"])
        record(action=AuditLog.Action.ACTIVATE, actor=request.user, shop=shop,
               target=shop, description="Shop activated by platform admin")
        return Response({"status": "active"})

    @action(detail=True, methods=["post"])
    def impersonate(self, request, pk=None):
        """
        Start impersonating a shop. Sets a session marker consumed by
        TenantMiddleware. Audited with the impersonator recorded explicitly.
        """
        shop = self.get_object()
        request.session[IMPERSONATE_SESSION_KEY] = shop.id
        record(
            action=AuditLog.Action.IMPERSONATE_START, actor=request.user,
            shop=shop, target=shop,
            description=f"Platform admin started impersonating '{shop.name}'",
            metadata={"impersonator_id": request.user.id},
        )
        return Response({"status": "impersonating", "shop_id": shop.id})


class StopImpersonationView(APIView):
    permission_classes = [IsPlatformStaff]

    def post(self, request):
        shop_id = request.session.pop(IMPERSONATE_SESSION_KEY, None)
        if shop_id is None:
            return Response({"status": "not_impersonating"},
                            status=status.HTTP_400_BAD_REQUEST)
        record(action=AuditLog.Action.IMPERSONATE_END, actor=request.user,
               description="Platform admin stopped impersonation",
               metadata={"shop_id": shop_id})
        return Response({"status": "stopped"})


# --- Manual billing review (8.7) ---------------------------------------------

class ManualPaymentAdminSerializer(serializers.ModelSerializer):
    shop_name = serializers.CharField(source="shop.name", read_only=True)

    class Meta:
        from billing.models import ManualPayment
        model = ManualPayment
        fields = [
            "id", "shop", "shop_name", "amount", "method", "payer_reference",
            "proof", "status", "submitted_at", "reviewed_at", "rejection_reason",
        ]


class ManualPaymentAdminViewSet(viewsets.ReadOnlyModelViewSet):
    """Super Admin queue for reviewing offline payments across all shops."""

    permission_classes = [IsPlatformStaff]
    serializer_class = ManualPaymentAdminSerializer

    def get_queryset(self):
        from billing.models import ManualPayment
        with bypass_tenant_scope():
            qs = ManualPayment.objects.select_related("shop").order_by("-submitted_at")
            if s := self.request.query_params.get("status"):
                qs = qs.filter(status=s)
            return qs

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        from billing.services import approve_payment
        payment = self.get_object()
        try:
            approve_payment(payment=payment, reviewer=request.user)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        record(action=AuditLog.Action.UPDATE, actor=request.user, shop=payment.shop,
               target=payment, description="Manual payment approved")
        return Response({"status": "approved"})

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        from billing.services import reject_payment
        payment = self.get_object()
        reason = request.data.get("reason", "")
        try:
            reject_payment(payment=payment, reviewer=request.user, reason=reason)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        record(action=AuditLog.Action.UPDATE, actor=request.user, shop=payment.shop,
               target=payment, description=f"Manual payment rejected: {reason}")
        return Response({"status": "rejected"})


class APIKeyAdminViewSet(viewsets.ModelViewSet):
    """Super Admin issues / revokes public-API keys per shop (9.7)."""

    permission_classes = [IsPlatformStaff]
    http_method_names = ["get", "post", "delete"]

    def get_serializer_class(self):
        from public_api.models import APIKey

        class APIKeySerializer(serializers.ModelSerializer):
            class Meta:
                model = APIKey
                fields = ["id", "shop", "name", "prefix", "can_read", "can_write",
                          "resources", "rate_tier", "is_active", "last_used_at", "created_at"]
                read_only_fields = ["prefix", "last_used_at"]
        return APIKeySerializer

    def get_queryset(self):
        from public_api.models import APIKey
        return APIKey.objects.select_related("shop").order_by("-created_at")

    def create(self, request, *args, **kwargs):
        from public_api.models import APIKey
        shop = Shop.objects.filter(pk=request.data.get("shop")).first()
        if shop is None:
            return Response({"detail": "Invalid shop."}, status=status.HTTP_400_BAD_REQUEST)
        instance, raw = APIKey.generate(
            shop=shop, name=request.data.get("name", "API key"),
            can_read=request.data.get("can_read", True),
            can_write=request.data.get("can_write", False),
            resources=request.data.get("resources", ["products", "inventory"]),
            rate_tier=request.data.get("rate_tier", APIKey.RateTier.STANDARD),
        )
        record(action=AuditLog.Action.CREATE, actor=request.user, shop=shop,
               target=instance, description="Issued public API key")
        data = self.get_serializer(instance).data
        data["raw_key"] = raw  # shown ONCE
        return Response(data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.is_active = False
        instance.save(update_fields=["is_active"])
        record(action=AuditLog.Action.UPDATE, actor=request.user, shop=instance.shop,
               target=instance, description="Revoked public API key")
        return Response({"status": "revoked"})


class RevenueByMethodView(APIView):
    """Approved manual-payment revenue grouped by method (offline reporting)."""

    permission_classes = [IsPlatformStaff]

    def get(self, request):
        from decimal import Decimal
        from django.db.models import DecimalField, Sum
        from django.db.models.functions import Coalesce

        from billing.models import ManualPayment
        with bypass_tenant_scope():
            rows = list(
                ManualPayment.objects.filter(status=ManualPayment.Status.APPROVED)
                .values("method")
                .annotate(total=Coalesce(
                    Sum("amount", output_field=DecimalField(max_digits=18, decimal_places=2)),
                    Decimal("0"), output_field=DecimalField(max_digits=18, decimal_places=2),
                ))
                .order_by("-total")
            )
        return Response({"by_method": rows})
