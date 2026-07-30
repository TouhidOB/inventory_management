from django.utils.dateparse import parse_datetime
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import HasPermCode, IsTenantMember
from core.tenant_context import set_current_tenant

from . import services


class _AnalyticsBase(APIView):
    permission_classes = [IsTenantMember, HasPermCode]
    required_perm = "view_reports"

    def initial(self, request, *args, **kwargs):
        set_current_tenant(getattr(request.user, "shop", None))
        request.tenant = getattr(request.user, "shop", None)
        super().initial(request, *args, **kwargs)

    @property
    def shop(self):
        return self.request.user.shop

    def _range(self):
        return (
            parse_datetime(self.request.query_params.get("start", "") or ""),
            parse_datetime(self.request.query_params.get("end", "") or ""),
        )


class DashboardView(_AnalyticsBase):
    def get(self, request):
        days = int(request.query_params.get("days", 30))
        return Response(services.dashboard_summary(self.shop, days=days))


class InventoryAnalyticsView(_AnalyticsBase):
    required_perm = "view_inventory"

    def get(self, request):
        return Response({
            "stock_value": services.stock_value(self.shop),
            "by_category": services.stock_by_category(self.shop),
            "by_brand": services.stock_by_brand(self.shop),
            "low_stock": services.low_stock_list(self.shop),
            "out_of_stock": services.out_of_stock_list(self.shop),
        })


class DeadStockView(_AnalyticsBase):
    required_perm = "view_inventory"

    def get(self, request):
        days = int(request.query_params.get("days", 90))
        return Response(services.dead_stock(self.shop, days=days))


class SalesRollupView(_AnalyticsBase):
    def get(self, request):
        return Response({
            "rollups": services.sales_rollups(self.shop),
            "weekly_trend": services.weekly_sales_trend(self.shop),
            "mom_growth": services.mom_growth(self.shop),
        })


class CategorySalesView(_AnalyticsBase):
    def get(self, request):
        period = request.query_params.get("period", "month")
        return Response(services.category_sales(self.shop, period=period))


class BrandSalesView(_AnalyticsBase):
    def get(self, request):
        period = request.query_params.get("period", "month")
        return Response(services.brand_sales(self.shop, period=period))


class TopProductsView(_AnalyticsBase):
    def get(self, request):
        start, end = self._range()
        limit = int(request.query_params.get("limit", 10))
        return Response(services.top_products(self.shop, start=start, end=end, limit=limit))


class ProductPerformanceView(_AnalyticsBase):
    def get(self, request, product_id):
        period = request.query_params.get("period", "month")
        return Response(services.product_performance(self.shop, product_id, period=period))


class SalesByCategoryView(_AnalyticsBase):
    def get(self, request):
        start, end = self._range()
        return Response(services.sales_by_category(self.shop, start=start, end=end))
