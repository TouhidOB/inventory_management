"""
Append-only stock ledger.

Every change to stock is a StockMovement row (immutable). A product's
``current_stock`` is a cache recomputed from the sum of its movements. This
gives full auditability — you can always reconstruct why stock is what it is.
"""
from django.db import models

from core.models import TenantScopedModel


class MovementType(models.TextChoices):
    OPENING = "opening", "Opening balance"
    PURCHASE_IN = "purchase_in", "Purchase received"
    SALE_OUT = "sale_out", "Sale"
    SALE_RETURN_IN = "sale_return_in", "Sale return"
    PURCHASE_RETURN_OUT = "purchase_return_out", "Purchase return"
    ADJUST_IN = "adjust_in", "Adjustment (in)"
    ADJUST_OUT = "adjust_out", "Adjustment (out)"
    DAMAGE_OUT = "damage_out", "Damaged / lost / expired"
    TRANSFER_IN = "transfer_in", "Branch transfer in"
    TRANSFER_OUT = "transfer_out", "Branch transfer out"


# Movement types that subtract from stock. Everything else adds.
OUTGOING_TYPES = {
    MovementType.SALE_OUT,
    MovementType.PURCHASE_RETURN_OUT,
    MovementType.ADJUST_OUT,
    MovementType.DAMAGE_OUT,
    MovementType.TRANSFER_OUT,
}


class StockMovement(TenantScopedModel):
    """One immutable stock event. ``quantity`` is always stored as a signed
    value (negative for outgoing) so summing the column yields current stock."""

    product = models.ForeignKey(
        "catalog.Product", on_delete=models.CASCADE, related_name="movements"
    )
    variation = models.ForeignKey(
        "catalog.ProductVariation", on_delete=models.CASCADE,
        null=True, blank=True, related_name="movements",
    )
    branch = models.ForeignKey(
        "tenants.Branch", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="stock_movements",
    )

    movement_type = models.CharField(max_length=30, choices=MovementType.choices)
    quantity = models.DecimalField(max_digits=14, decimal_places=2)  # signed
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Loose reference back to the source document (Sale, PurchaseOrder, ...).
    reference_type = models.CharField(max_length=40, blank=True)
    reference_id = models.CharField(max_length=64, blank=True)
    note = models.CharField(max_length=255, blank=True)

    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="stock_movements",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["shop", "product", "created_at"]),
            models.Index(fields=["reference_type", "reference_id"]),
        ]

    def __str__(self):
        return f"{self.movement_type} {self.quantity} of product#{self.product_id}"
