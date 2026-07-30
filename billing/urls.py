from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    BillingDetailsView,
    ManualPaymentViewSet,
    PlanListView,
    SubscriptionStatusView,
)

router = DefaultRouter()
router.register("payments", ManualPaymentViewSet, basename="manual-payment")

urlpatterns = [
    path("plans/", PlanListView.as_view(), name="plans"),
    path("status/", SubscriptionStatusView.as_view(), name="status"),
    path("details/", BillingDetailsView.as_view(), name="billing-details"),
] + router.urls
