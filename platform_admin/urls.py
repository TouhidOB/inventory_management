from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    APIKeyAdminViewSet,
    ManualPaymentAdminViewSet,
    PlatformDashboardView,
    RevenueByMethodView,
    ShopAdminViewSet,
    StopImpersonationView,
)

app_name = "platform_admin"

router = DefaultRouter()
router.register("shops", ShopAdminViewSet, basename="admin-shops")
router.register("manual-payments", ManualPaymentAdminViewSet, basename="admin-manual-payments")
router.register("api-keys", APIKeyAdminViewSet, basename="admin-api-keys")

urlpatterns = [
    path("dashboard/", PlatformDashboardView.as_view(), name="dashboard"),
    path("revenue-by-method/", RevenueByMethodView.as_view(), name="revenue-by-method"),
    path("impersonate/stop/", StopImpersonationView.as_view(), name="impersonate_stop"),
    path("", include(router.urls)),
]
