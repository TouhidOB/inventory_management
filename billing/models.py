"""
Manual / offline billing (8.7).

NO payment gateway. Owners pay outside the platform (bKash/Nagad/bank/cash),
then submit a ManualPayment with proof. A Super Admin approves or rejects.
Approval extends the subscription and marks the invoice paid. Every taka that
moves is a human action recorded after the fact.
"""
from django.db import models

from core.models import TenantScopedModel


class SubscriptionInvoice(TenantScopedModel):
    class Status(models.TextChoices):
        UNPAID = "unpaid", "Unpaid"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    subscription = models.ForeignKey(
        "tenants.Subscription", on_delete=models.CASCADE, related_name="invoices"
    )
    plan = models.ForeignKey("tenants.SubscriptionPlan", on_delete=models.PROTECT, related_name="invoices")
    number = models.CharField(max_length=40, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    cycle = models.CharField(max_length=10, default="monthly")
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.UNPAID)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.number or f"SUB-INV#{self.pk}"


class ManualPayment(TenantScopedModel):
    class Method(models.TextChoices):
        BKASH = "bkash", "bKash"
        NAGAD = "nagad", "Nagad"
        BANK_TRANSFER = "bank_transfer", "Bank transfer"
        CASH = "cash", "Cash"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PENDING = "pending_review", "Pending review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    subscription = models.ForeignKey(
        "tenants.Subscription", on_delete=models.CASCADE, related_name="manual_payments"
    )
    invoice = models.ForeignKey(
        SubscriptionInvoice, on_delete=models.SET_NULL, null=True, blank=True, related_name="payments"
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=20, choices=Method.choices)
    payer_reference = models.CharField(max_length=120, help_text="Txn ID / sender number entered by owner")
    proof = models.FileField(upload_to="payment_proofs/", null=True, blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)
    submitted_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="submitted_payments"
    )

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    reviewed_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="reviewed_payments"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-submitted_at"]
        indexes = [models.Index(fields=["status", "submitted_at"])]

    def __str__(self):
        return f"{self.amount} via {self.method} [{self.status}]"
