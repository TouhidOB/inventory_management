from django.urls import path

from .views import (
    BrandSalesView,
    CategorySalesView,
    DashboardView,
    DeadStockView,
    InventoryAnalyticsView,
    ProductPerformanceView,
    SalesByCategoryView,
    SalesRollupView,
    TopProductsView,
)

urlpatterns = [
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
    path("inventory/", InventoryAnalyticsView.as_view(), name="inventory-analytics"),
    path("dead-stock/", DeadStockView.as_view(), name="dead-stock"),
    path("sales-rollup/", SalesRollupView.as_view(), name="sales-rollup"),
    path("category-sales/", CategorySalesView.as_view(), name="category-sales"),
    path("brand-sales/", BrandSalesView.as_view(), name="brand-sales"),
    path("top-products/", TopProductsView.as_view(), name="top-products"),
    path("product-performance/<int:product_id>/", ProductPerformanceView.as_view(), name="product-performance"),
    path("sales-by-category/", SalesByCategoryView.as_view(), name="sales-by-category"),
]
