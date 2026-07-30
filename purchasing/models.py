"""Suppliers, purchase orders, goods received, supplier dues."""
from django.db import models

from core.models import TenantScopedModel


class Supplier(TenantScopedModel):
    name = models.CharField(max_length=150)
    company_name = models.CharField(max_length=180, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    # Cached payable. Positive => shop owes the supplier.
    due_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0, editable=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class PurchaseOrder(TenantScopedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ORDERED = "ordered", "Ordered"
        RECEIVED = "received", "Received"
        CANCELLED = "cancelled", "Cancelled"

    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="purchase_orders")
    branch = models.ForeignKey(
        "tenants.Branch", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="purchase_orders",
    )
    po_number = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    order_date = models.DateField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)

    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    paid = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="purchase_orders",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.po_number or f"PO#{self.pk}"

    @property
    def due(self):
        return self.total - self.paid


class PurchaseOrderItem(TenantScopedModel):
    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name="items"
    )
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="purchase_items")
    variation = models.ForeignKey(
        "catalog.ProductVariation", on_delete=models.PROTECT,
        null=True, blank=True, related_name="purchase_items",
    )
    quantity = models.DecimalField(max_digits=14, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    def save(self, *args, **kwargs):
        self.subtotal = (self.quantity or 0) * (self.unit_cost or 0)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.quantity} x {self.product_id} @ {self.unit_cost}"


class PurchasePayment(TenantScopedModel):
    """A payment made against a specific purchase order. Multiple partial
    payments can accumulate over time until the PO's due reaches zero. Each
    payment bumps ``PurchaseOrder.paid``, lowers the supplier's cached
    ``due_balance`` and is posted to the accounting tables."""

    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        BKASH = "bkash", "bKash"
        NAGAD = "nagad", "Nagad"
        BANK = "bank", "Bank transfer"

    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name="payments"
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    method = models.CharField(max_length=20, choices=Method.choices, default=Method.CASH)
    reference = models.CharField(max_length=100, blank=True)
    note = models.CharField(max_length=255, blank=True)
    paid_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="purchase_payments",
    )

    class Meta:
        ordering = ["-paid_at"]

    def __str__(self):
        return f"{self.amount} for PO#{self.purchase_order_id}"


class SupplierPayment(TenantScopedModel):
    """A payment made to a supplier against their outstanding payable. Reduces
    the supplier's cached ``due_balance`` and records a cash outflow."""

    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        BKASH = "bkash", "bKash"
        NAGAD = "nagad", "Nagad"
        BANK = "bank", "Bank transfer"

    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    method = models.CharField(max_length=20, choices=Method.choices, default=Method.CASH)
    reference = models.CharField(max_length=100, blank=True)
    note = models.CharField(max_length=255, blank=True)
    paid_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="supplier_payments",
    )

    class Meta:
        ordering = ["-paid_at"]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="supplier_payment_amount_positive"),
        ]

    def __str__(self):
        return f"{self.amount} to supplier#{self.supplier_id}"
