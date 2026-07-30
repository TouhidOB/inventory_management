from rest_framework import mixins, serializers, viewsets
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsTenantMember
from core.tenant_context import set_current_tenant
from tenants.models import Subscription, SubscriptionPlan

from .models import ManualPayment, SubscriptionInvoice
from .services import billing_details, submit_manual_payment, subscription_status


class _Scoped:
    def initial(self, request, *args, **kwargs):
        set_current_tenant(getattr(request.user, "shop", None))
        request.tenant = getattr(request.user, "shop", None)
        super().initial(request, *args, **kwargs)


class PlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionPlan
        fields = ["id", "name", "tier", "price_monthly", "price_yearly",
                  "features", "max_users", "max_branches", "max_products"]


class PlanListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        plans = SubscriptionPlan.objects.filter(is_active=True)
        return Response(PlanSerializer(plans, many=True).data)


class SubscriptionStatusView(_Scoped, APIView):
    permission_classes = [IsTenantMember]

    def get(self, request):
        return Response(subscription_status(request.user.shop))


class BillingDetailsView(APIView):
    """Platform receiving numbers for owners to send manual payment to."""

    permission_classes = [IsTenantMember]

    def get(self, request):
        return Response(billing_details())


class ManualPaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = ManualPayment
        fields = ["id", "subscription", "invoice", "amount", "method", "payer_reference",
                  "proof", "status", "submitted_at", "reviewed_at", "rejection_reason"]
        read_only_fields = ["subscription", "invoice", "status", "submitted_at",
                            "reviewed_at", "rejection_reason"]


class ManualPaymentSubmitSerializer(serializers.Serializer):
    plan = serializers.PrimaryKeyRelatedField(queryset=SubscriptionPlan.objects.all())
    cycle = serializers.ChoiceField(choices=Subscription.Cycle.choices, default=Subscription.Cycle.MONTHLY)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    method = serializers.ChoiceField(choices=ManualPayment.Method.choices)
    payer_reference = serializers.CharField(max_length=120)
    proof = serializers.FileField(required=False, allow_null=True)


class ManualPaymentViewSet(_Scoped, mixins.ListModelMixin, viewsets.GenericViewSet):
    """Owner submits and lists their manual payments."""

    permission_classes = [IsTenantMember]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    serializer_class = ManualPaymentSerializer

    def get_queryset(self):
        return ManualPayment.objects.select_related("invoice", "subscription")

    def create(self, request, *args, **kwargs):
        if request.user.role != "owner":
            return Response({"detail": "Only the owner can submit payments."}, status=403)
        ser = ManualPaymentSubmitSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        payment = submit_manual_payment(
            shop=request.user.shop, plan=d["plan"], cycle=d["cycle"],
            amount=d["amount"], method=d["method"],
            payer_reference=d["payer_reference"], proof=d.get("proof"),
            submitted_by=request.user,
        )
        return Response(ManualPaymentSerializer(payment).data, status=201)
