from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import BranchComparisonView, BranchViewSet, StockTransferViewSet

router = DefaultRouter()
router.register("branches", BranchViewSet, basename="branch")
router.register("stock-transfers", StockTransferViewSet, basename="stock-transfer")

urlpatterns = [
    path("comparison/", BranchComparisonView.as_view(), name="branch-comparison"),
] + router.urls
