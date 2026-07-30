from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    CustomerV1ViewSet,
    InventoryV1View,
    ProductV1ViewSet,
    ReportV1View,
    SaleV1ViewSet,
)

router = DefaultRouter()
router.register("products", ProductV1ViewSet, basename="v1-product")
router.register("customers", CustomerV1ViewSet, basename="v1-customer")
router.register("sales", SaleV1ViewSet, basename="v1-sale")

urlpatterns = [
    path("inventory/", InventoryV1View.as_view(), name="v1-inventory"),
    path("reports/", ReportV1View.as_view(), name="v1-reports"),
] + router.urls
