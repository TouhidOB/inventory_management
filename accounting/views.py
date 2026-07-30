from django.utils.dateparse import parse_datetime
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api import TenantScopedViewSet
from core.permissions import HasPermCode, IsTenantMember
from core.tenant_context import set_current_tenant

from .models import Expense, ExpenseCategory
from .serializers import ExpenseCategorySerializer, ExpenseSerializer
from .services import cash_flow, financial_position, profit_summary, record_expense


class ExpenseCategoryViewSet(TenantScopedViewSet):
    serializer_class = ExpenseCategorySerializer
    required_perm = "manage_expenses"

    def get_queryset(self):
        return ExpenseCategory.objects.all()


class ExpenseViewSet(TenantScopedViewSet):
    serializer_class = ExpenseSerializer
    required_perm = "manage_expenses"

    def get_queryset(self):
        return Expense.objects.select_related("category")

    def perform_create(self, serializer):
        # Route through the service so the cash ledger stays in sync.
        v = serializer.validated_data
        expense = record_expense(
            shop=self.request.user.shop, amount=v["amount"], spent_on=v["spent_on"],
            category=v.get("category"), payment_method=v.get("payment_method", ""),
            note=v.get("note", ""), created_by=self.request.user,
        )
        serializer.instance = expense


class _ReportBase(APIView):
    permission_classes = [IsTenantMember, HasPermCode]
    required_perm = "view_profit"

    def initial(self, request, *args, **kwargs):
        set_current_tenant(getattr(request.user, "shop", None))
        request.tenant = getattr(request.user, "shop", None)
        super().initial(request, *args, **kwargs)


class ProfitReportView(_ReportBase):
    def get(self, request):
        start = parse_datetime(request.query_params.get("start", "") or "")
        end = parse_datetime(request.query_params.get("end", "") or "")
        return Response(profit_summary(request.user.shop, start=start, end=end))


class FinancialPositionView(_ReportBase):
    def get(self, request):
        return Response(financial_position(request.user.shop))


class CashFlowView(_ReportBase):
    def get(self, request):
        start = parse_datetime(request.query_params.get("start", "") or "")
        end = parse_datetime(request.query_params.get("end", "") or "")
        return Response(cash_flow(request.user.shop, start=start, end=end))
