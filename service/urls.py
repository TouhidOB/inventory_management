from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    ServiceDashboardView,
    ServiceTicketViewSet,
    WarrantyClaimViewSet,
    WarrantyViewSet,
)

router = DefaultRouter()
router.register("warranties", WarrantyViewSet, basename="warranty")
router.register("warranty-claims", WarrantyClaimViewSet, basename="warranty-claim")
router.register("tickets", ServiceTicketViewSet, basename="service-ticket")

urlpatterns = [
    path("dashboard/", ServiceDashboardView.as_view(), name="service-dashboard"),
] + router.urls
