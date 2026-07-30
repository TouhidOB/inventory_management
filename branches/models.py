"""Stock transfers between branches (8.5). Branch itself lives in `tenants`."""
from django.db import models

from core.models import TenantScopedModel


class StockTransfer(TenantScopedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_TRANSIT = "in_transit", "In transit"
        RECEIVED = "received", "Received"
        CANCELLED = "cancelled", "Cancelled"

    source_branch = models.ForeignKey(
        "tenants.Branch", on_delete=models.PROTECT, related_name="transfers_out"
    )
    dest_branch = models.ForeignKey(
        "tenants.Branch", on_delete=models.PROTECT, related_name="transfers_in"
    )
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING)
    note = models.CharField(max_length=255, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="stock_transfers"
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Transfer #{self.pk}: {self.source_branch_id} -> {self.dest_branch_id}"


class StockTransferItem(TenantScopedModel):
    transfer = models.ForeignKey(StockTransfer, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="transfer_items")
    variation = models.ForeignKey(
        "catalog.ProductVariation", on_delete=models.PROTECT, null=True, blank=True, related_name="transfer_items"
    )
    quantity = models.DecimalField(max_digits=14, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def __str__(self):
        return f"{self.quantity} x {self.product_id}"
