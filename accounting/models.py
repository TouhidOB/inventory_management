"""
Accounting primitives: expenses + a simple cash ledger.

Profit is computed transparently in ``services`` — never faked as
selling_price − purchase_price. COGS comes from snapshotted sale-item costs;
returns and expenses are subtracted explicitly.
"""
from django.db import models

from core.models import TenantScopedModel


class ExpenseCategory(TenantScopedModel):
    name = models.CharField(max_length=120)

    class Meta:
        verbose_name_plural = "expense categories"
        constraints = [
            models.UniqueConstraint(fields=["shop", "name"], name="uniq_expense_cat_per_shop"),
        ]

    def __str__(self):
        return self.name


class Expense(TenantScopedModel):
    category = models.ForeignKey(
        ExpenseCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name="expenses"
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    spent_on = models.DateField()
    payment_method = models.CharField(max_length=20, blank=True)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="expenses"
    )

    class Meta:
        ordering = ["-spent_on"]
        indexes = [models.Index(fields=["shop", "spent_on"])]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gte=0), name="expense_amount_non_negative"),
        ]

    def __str__(self):
        return f"{self.amount} on {self.spent_on}"


class RecurringExpense(TenantScopedModel):
    """A template that a Celery task materializes into an Expense monthly."""

    category = models.ForeignKey(
        ExpenseCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name="recurring"
    )
    label = models.CharField(max_length=120)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    day_of_month = models.PositiveSmallIntegerField(default=1)  # 1..28
    payment_method = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)
    last_generated_period = models.CharField(max_length=7, blank=True)  # "YYYY-MM"

    def __str__(self):
        return f"{self.label} ({self.amount}/mo)"


class LedgerEntry(TenantScopedModel):
    """
    Append-only cash/bank ledger for auditability. Positive = money in,
    negative = money out. Sources: sale payments, expenses, supplier payments.
    """

    class Account(models.TextChoices):
        CASH = "cash", "Cash"
        BANK = "bank", "Bank"

    account = models.CharField(max_length=10, choices=Account.choices, default=Account.CASH)
    amount = models.DecimalField(max_digits=14, decimal_places=2)  # signed
    source_type = models.CharField(max_length=40, blank=True)
    source_id = models.CharField(max_length=64, blank=True)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["shop", "account", "created_at"])]

    def __str__(self):
        return f"{self.account} {self.amount}"
