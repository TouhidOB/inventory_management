"""Sales, invoices, line items, payments, returns."""
from django.db import models

from core.models import TenantScopedModel


class Sale(TenantScopedModel):
    """A sale IS the invoice. ``invoice_no`` is the human reference."""

    class Status(models.TextChoices):
        PAID = "paid", "Paid"
        PARTIAL = "partial", "Partial"
        DUE = "due", "Due"
        CANCELLED = "cancelled", "Cancelled"
        RETURNED = "returned", "Returned"
        PARTIALLY_RETURNED = "partially_returned", "Partially returned"

    invoice_no = models.CharField(max_length=40, db_index=True)
    customer = models.ForeignKey(
        "crm.Customer", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="sales",  # null => walk-in customer
    )
    # Walk-in details captured at POS when no saved Customer is chosen. These
    # print on the invoice so a one-off buyer still gets their name/contact on
    # the receipt without polluting the CRM.
    customer_name = models.CharField(max_length=150, blank=True)
    customer_phone = models.CharField(max_length=30, blank=True)
    customer_address = models.TextField(blank=True)
    branch = models.ForeignKey(
        "tenants.Branch", on_delete=models.SET_NULL, null=True, blank=True, related_name="sales"
    )
    sale_date = models.DateTimeField()

    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    paid = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DUE)
    note = models.CharField(max_length=255, blank=True)
    # Client-supplied token that makes checkout idempotent: a rapid double-click
    # (or a refresh/retry mid-request) reuses the same key, so the second request
    # returns the first sale instead of creating a duplicate.
    idempotency_key = models.CharField(max_length=64, blank=True, default="", db_index=True)
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="sales"
    )

    class Meta:
        ordering = ["-sale_date"]
        indexes = [models.Index(fields=["shop", "sale_date"])]
        constraints = [
            models.UniqueConstraint(fields=["shop", "invoice_no"], name="uniq_invoice_per_shop"),
            # Enforce at-most-one sale per (shop, idempotency_key) — but only when
            # a key was supplied, so legacy/keyless sales are unaffected.
            models.UniqueConstraint(
                fields=["shop", "idempotency_key"],
                condition=models.Q(idempotency_key__gt=""),
                name="uniq_sale_idempotency_key",
            ),
        ]

    def __str__(self):
        return self.invoice_no

    @property
    def due(self):
        return self.total - self.paid

    # Invoice "Bill to" values: prefer the saved Customer, fall back to the
    # walk-in fields captured at POS.
    @property
    def bill_name(self):
        return (self.customer.name if self.customer_id else self.customer_name) or "Walk-in customer"

    @property
    def bill_phone(self):
        return self.customer.phone if self.customer_id else self.customer_phone

    @property
    def bill_address(self):
        return self.customer.address if self.customer_id else self.customer_address


class SaleItem(TenantScopedModel):
    """
    A sold line. ``unit_cost`` is SNAPSHOTTED at sale time so COGS/profit
    stays honest even if the product's cost changes later.
    """

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="sale_items")
    variation = models.ForeignKey(
        "catalog.ProductVariation", on_delete=models.PROTECT,
        null=True, blank=True, related_name="sale_items",
    )
    quantity = models.DecimalField(max_digits=14, decimal_places=2)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)  # snapshot
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    def save(self, *args, **kwargs):
        self.subtotal = (self.quantity or 0) * (self.unit_price or 0) - (self.discount or 0)
        super().save(*args, **kwargs)

    @property
    def cogs(self):
        return (self.quantity or 0) * (self.unit_cost or 0)

    def __str__(self):
        return f"{self.quantity} x {self.product_id}"


class Payment(TenantScopedModel):
    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        CARD = "card", "Card"
        BKASH = "bkash", "bKash"
        NAGAD = "nagad", "Nagad"
        BANK = "bank", "Bank transfer"

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    method = models.CharField(max_length=20, choices=Method.choices, default=Method.CASH)
    paid_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="payment_amount_positive"),
        ]

    def __str__(self):
        return f"{self.amount} ({self.method}) for {self.sale_id}"


class SaleReturn(TenantScopedModel):
    """A (partial) return against a sale. Restocks + reduces receivables.
    Refund is manual/offline — we record the method + reference only."""

    class RefundMethod(models.TextChoices):
        CASH = "cash", "Cash"
        BKASH = "bkash", "bKash"
        NAGAD = "nagad", "Nagad"
        BANK = "bank", "Bank transfer"
        STORE_CREDIT = "store_credit", "Store credit"
        EXCHANGE = "exchange", "Exchange (no cash)"

    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="returns")
    reason = models.CharField(max_length=255, blank=True)
    # Whether the returned goods went back into sellable stock. When True the P&L
    # credits their COGS back (they weren't really sold); when False (damaged /
    # discarded) the cost stays as a loss.
    restocked = models.BooleanField(default=True)
    total_refund = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    refund_method = models.CharField(max_length=20, choices=RefundMethod.choices, default=RefundMethod.CASH)
    refund_reference = models.CharField(max_length=100, blank=True)
    # Set when this return is part of an exchange (points at the new sale).
    exchange_sale = models.ForeignKey(
        Sale, on_delete=models.SET_NULL, null=True, blank=True, related_name="exchanged_from"
    )
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="sale_returns"
    )

    def __str__(self):
        return f"Return of {self.sale_id}"


class SaleReturnItem(TenantScopedModel):
    sale_return = models.ForeignKey(SaleReturn, on_delete=models.CASCADE, related_name="items")
    sale_item = models.ForeignKey(SaleItem, on_delete=models.PROTECT, related_name="return_items")
    quantity = models.DecimalField(max_digits=14, decimal_places=2)
    refund_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    def __str__(self):
        return f"{self.quantity} of item#{self.sale_item_id}"
