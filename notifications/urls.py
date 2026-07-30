from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    AlertConfigView,
    NotificationViewSet,
    StockAlertsWidgetView,
    WhatsAppConfigView,
    WhatsAppWebhookView,
)

router = DefaultRouter()
router.register("notifications", NotificationViewSet, basename="notification")

urlpatterns = [
    path("alert-config/", AlertConfigView.as_view(), name="alert-config"),
    path("stock-alerts/", StockAlertsWidgetView.as_view(), name="stock-alerts-widget"),
    path("whatsapp/config/", WhatsAppConfigView.as_view(), name="whatsapp-config"),
    path("whatsapp/webhook/", WhatsAppWebhookView.as_view(), name="whatsapp-webhook"),
] + router.urls
