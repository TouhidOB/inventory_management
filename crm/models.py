"""Customer profiles + due tracking + segmentation."""
from django.db import models
from django.utils import timezone

from core.models import TenantScopedModel


class Customer(TenantScopedModel):
    class Segment(models.TextChoices):
        NEW = "new", "New"
        REGULAR = "regular", "Regular"
        VIP = "vip", "VIP"
        INACTIVE = "inactive", "Inactive"

    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, blank=True, db_index=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    segment = models.CharField(max_length=20, choices=Segment.choices, default=Segment.NEW)
    # Default discount applied at POS for this customer (percent of line total).
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    # Cached running due (receivable). Positive => customer owes the shop.
    due_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0, editable=False)
    total_purchased = models.DecimalField(max_digits=14, decimal_places=2, default=0, editable=False)
    last_purchase_at = models.DateTimeField(null=True, blank=True)

    is_active = models.BooleanField(default=True)

    # WhatsApp opt-in (9.5). Never message a customer without consent.
    whatsapp_consent = models.BooleanField(default=False)
    whatsapp_opt_in_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["shop", "phone"])]

    def __str__(self):
        return self.name

    @property
    def days_since_last_purchase(self):
        if not self.last_purchase_at:
            return None
        return (timezone.now() - self.last_purchase_at).days
