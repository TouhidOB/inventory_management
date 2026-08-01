"""Warranty tracking (9.1) + service tickets (9.2)."""
from datetime import timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.db import models
from django.utils import timezone

from core.models import TenantScopedModel


class Warranty(TenantScopedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRING_SOON = "expiring_soon", "Expiring soon"
        EXPIRED = "expired", "Expired"
        CLAIMED = "claimed", "Claimed"
        VOID = "void", "Void"

    # Prefer linking to the exact sold line; fall back to product+customer.
    sale_item = models.ForeignKey(
        "sales.SaleItem", on_delete=models.SET_NULL, null=True, blank=True, related_name="warranties"
    )
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="warranties")
    # The exact physical unit (serial/barcode) this warranty covers. Set when a
    # bulk purchase is received: one Warranty per ProductUnit, so a batch is
    # bought together but returned/claimed one unit at a time.
    product_unit = models.ForeignKey(
        "catalog.ProductUnit", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="warranties",
    )
    customer = models.ForeignKey(
        "crm.Customer", on_delete=models.SET_NULL, null=True, blank=True, related_name="warranties"
    )
    serial_no = models.CharField(max_length=100, blank=True)
    period_months = models.PositiveSmallIntegerField(default=12)
    start_date = models.DateField(default=timezone.localdate)
    expiry_date = models.DateField(editable=False)
    terms = models.TextField(blank=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        ordering = ["expiry_date"]
        indexes = [models.Index(fields=["shop", "expiry_date"])]

    def __str__(self):
        return f"Warranty {self.product_id} until {self.expiry_date}"

    def save(self, *args, **kwargs):
        self.expiry_date = self.start_date + relativedelta(months=self.period_months)
        super().save(*args, **kwargs)

    def compute_status(self, soon_days=30):
        """Return the status the dates imply (does not persist)."""
        if self.status in {self.Status.CLAIMED, self.Status.VOID}:
            return self.status
        today = timezone.localdate()
        if self.expiry_date < today:
            return self.Status.EXPIRED
        if self.expiry_date <= today + timedelta(days=soon_days):
            return self.Status.EXPIRING_SOON
        return self.Status.ACTIVE


class WarrantyClaim(TenantScopedModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In progress"
        RESOLVED = "resolved", "Resolved"
        REJECTED = "rejected", "Rejected"

    warranty = models.ForeignKey(Warranty, on_delete=models.CASCADE, related_name="claims")
    claim_date = models.DateField(default=timezone.localdate)
    expected_return_date = models.DateField(null=True, blank=True)
    issue_description = models.TextField()
    resolution = models.TextField(blank=True)
    resolved_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="warranty_claims"
    )
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.OPEN)

    def __str__(self):
        return f"Claim on warranty#{self.warranty_id} [{self.status}]"


class ServiceTicket(TenantScopedModel):
    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        DIAGNOSING = "diagnosing", "Diagnosing"
        AWAITING_PARTS = "awaiting_parts", "Awaiting parts"
        IN_REPAIR = "in_repair", "In repair"
        READY = "ready_for_pickup", "Ready for pickup"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"

    NOTIFY_STATUSES = {Status.READY, Status.AWAITING_PARTS}

    class DeviceType(models.TextChoices):
        LAPTOP = "laptop", "Laptop"
        DESKTOP = "desktop", "Desktop"
        CONSOLE = "console", "Gaming console"
        PHONE = "phone", "Phone"
        TABLET = "tablet", "Tablet"
        OTHER = "other", "Other"

    class IssueType(models.TextChoices):
        SCREEN = "screen", "Cracked / faulty screen"
        BATTERY = "battery", "Battery / charging"
        SOFTWARE = "software", "OS / software"
        LIQUID = "liquid", "Liquid damage"
        POWER = "power", "Boot / power"
        VIRUS = "virus", "Virus / malware"
        OTHER = "other", "Other"

    ticket_no = models.CharField(max_length=40, db_index=True)
    branch = models.ForeignKey(
        "tenants.Branch", on_delete=models.SET_NULL, null=True, blank=True, related_name="service_tickets"
    )
    customer = models.ForeignKey(
        "crm.Customer", on_delete=models.SET_NULL, null=True, blank=True, related_name="service_tickets"
    )
    # Free-text walk-in identity (no Customer record required).
    customer_name = models.CharField(max_length=150, blank=True)
    customer_phone = models.CharField(max_length=30, blank=True)
    # Warranty this repair is claimed against (item 13), if any.
    warranty = models.ForeignKey(
        "service.Warranty", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="service_tickets",
    )
    device_description = models.CharField(max_length=200)
    # Structured taxonomy powering repair-analytics charts (volume by device,
    # issue Pareto). Free-text description/complaint kept for detail.
    device_type = models.CharField(
        max_length=20, choices=DeviceType.choices, default=DeviceType.OTHER, db_index=True
    )
    issue_type = models.CharField(
        max_length=20, choices=IssueType.choices, default=IssueType.OTHER, db_index=True
    )
    complaint = models.TextField()
    received_at = models.DateTimeField(default=timezone.now)
    technician = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="service_tickets"
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RECEIVED)
    service_charge = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    estimated_delivery = models.DateField(null=True, blank=True)
    actual_delivery = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="created_tickets"
    )

    class Meta:
        ordering = ["-received_at"]
        constraints = [
            models.UniqueConstraint(fields=["shop", "ticket_no"], name="uniq_ticket_no_per_shop"),
        ]

    def __str__(self):
        return self.ticket_no

    @property
    def is_overdue(self):
        if self.estimated_delivery and self.status not in {self.Status.DELIVERED, self.Status.CANCELLED}:
            return self.estimated_delivery < timezone.localdate()
        return False

    @property
    def parts_total(self):
        return sum((p.line_total for p in self.parts.all()), Decimal("0"))

    @property
    def bill_total(self):
        """Customer bill = labour (service_charge) + parts at their sell price."""
        return (self.service_charge or Decimal("0")) + self.parts_total

    @property
    def due(self):
        return self.bill_total - (self.paid or Decimal("0"))


class ServiceTicketPart(TenantScopedModel):
    """A part consumed on a ticket. If ``from_stock`` the product stock is
    deducted through the inventory ledger when the part is recorded."""

    ticket = models.ForeignKey(ServiceTicket, on_delete=models.CASCADE, related_name="parts")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="service_parts")
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=1)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # What the customer is charged for this part (unit_cost is COGS). Defaults to
    # the product's selling price when the part is added.
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    from_stock = models.BooleanField(default=True)

    @property
    def line_total(self):
        return (self.quantity or 0) * (self.unit_price or 0)

    def __str__(self):
        return f"{self.quantity} x {self.product_id} on ticket#{self.ticket_id}"


class ServiceTicketStatusHistory(TenantScopedModel):
    ticket = models.ForeignKey(ServiceTicket, on_delete=models.CASCADE, related_name="history")
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20)
    note = models.CharField(max_length=255, blank=True)
    changed_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="ticket_changes"
    )

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.from_status} -> {self.to_status}"
