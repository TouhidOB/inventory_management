"""In-app / email / SMS / WhatsApp notifications + per-shop alert config."""
from django.db import models

from core.models import TenantScopedModel


class NotificationType(models.TextChoices):
    LOW_STOCK = "low_stock", "Low stock"
    OUT_OF_STOCK = "out_of_stock", "Out of stock"
    PAYMENT_DUE = "payment_due", "Payment due"
    SUBSCRIPTION = "subscription", "Subscription"
    GENERAL = "general", "General"


class Notification(TenantScopedModel):
    """One in-app notification. Delivery to other channels tracked in
    ``sent_channels`` (a list of channel names actually dispatched)."""

    # Null user => shop-wide (visible to all shop staff, typically owners).
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, null=True, blank=True,
        related_name="notifications",
    )
    type = models.CharField(max_length=30, choices=NotificationType.choices, default=NotificationType.GENERAL)
    title = models.CharField(max_length=150)
    message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    sent_channels = models.JSONField(default=list, blank=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["shop", "is_read", "created_at"])]

    def __str__(self):
        return f"[{self.type}] {self.title}"


class ShopAlertConfig(TenantScopedModel):
    """Per-shop stock-alert configuration."""

    class Mode(models.TextChoices):
        REALTIME = "realtime", "Real-time"
        DAILY = "daily", "Daily digest"

    low_stock_enabled = models.BooleanField(default=True)
    mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.DAILY)
    email_enabled = models.BooleanField(default=True)
    sms_enabled = models.BooleanField(default=False)
    whatsapp_enabled = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["shop"], name="uniq_alert_config_per_shop"),
        ]

    def __str__(self):
        return f"AlertConfig({self.shop_id})"


class ShopWhatsAppConfig(TenantScopedModel):
    """
    Per-shop WhatsApp sender config (9.5). A shop uses either the shared
    platform number or, for Enterprise, its own Meta Business API credentials.
    """

    enabled = models.BooleanField(default=False)
    use_platform_number = models.BooleanField(default=True)
    # Only used when NOT on the platform number (Enterprise own creds).
    phone_number_id = models.CharField(max_length=60, blank=True)
    access_token = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["shop"], name="uniq_whatsapp_config_per_shop"),
        ]

    def __str__(self):
        return f"WhatsAppConfig({self.shop_id})"
