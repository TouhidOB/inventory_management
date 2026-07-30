"""Branch stock-transfer + branch analytics services (8.5)."""
from decimal import Decimal

from django.db import transaction
from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from inventory.models import MovementType, StockMovement
from inventory.services import apply_movement
from sales.models import Sale, SaleItem
from tenants.models import Branch

from .models import StockTransfer

ZERO = Decimal("0")
_DEC = DecimalField(max_digits=18, decimal_places=2)


@transaction.atomic
def create_transfer(*, shop, source_branch, dest_branch, items, note="", created_by=None):
    if source_branch == dest_branch:
        raise ValueError("Source and destination branches must differ.")
    transfer = StockTransfer.objects.create(
        shop=shop, source_branch=source_branch, dest_branch=dest_branch,
        status=StockTransfer.Status.IN_TRANSIT, note=note, created_by=created_by,
    )
    for row in items:
        transfer.items.create(
            shop=shop, product=row["product"], variation=row.get("variation"),
            quantity=Decimal(row["quantity"]), unit_cost=Decimal(row.get("unit_cost", 0)),
        )
    return transfer


@transaction.atomic
def receive_transfer(*, transfer, created_by=None):
    """
    On receipt write both legs to the ledger: TRANSFER_OUT at source and
    TRANSFER_IN at dest. Product-level stock nets zero (shop-wide); the branch
    allocation is what changes (tracked via movement.branch).
    """
    if transfer.status == StockTransfer.Status.RECEIVED:
        raise ValueError("Transfer already received.")

    for item in transfer.items.select_related("product", "variation").all():
        apply_movement(
            shop=transfer.shop, product=item.product, variation=item.variation,
            movement_type=MovementType.TRANSFER_OUT, quantity=item.quantity,
            unit_cost=item.unit_cost, branch=transfer.source_branch,
            reference_type="StockTransfer", reference_id=transfer.id, created_by=created_by,
        )
        apply_movement(
            shop=transfer.shop, product=item.product, variation=item.variation,
            movement_type=MovementType.TRANSFER_IN, quantity=item.quantity,
            unit_cost=item.unit_cost, branch=transfer.dest_branch,
            reference_type="StockTransfer", reference_id=transfer.id, created_by=created_by,
        )
    transfer.status = StockTransfer.Status.RECEIVED
    transfer.received_at = timezone.now()
    transfer.save(update_fields=["status", "received_at"])
    return transfer


def branch_stock_value(shop, branch):
    """Sum of (branch stock qty * unit_cost) from ledger movements at a branch."""
    rows = (
        StockMovement.all_objects.filter(shop_id=shop.id, branch=branch)
        .values("product_id")
        .annotate(qty=Coalesce(Sum("quantity", output_field=_DEC), ZERO, output_field=_DEC))
    )
    total = ZERO
    from catalog.models import Product
    costs = dict(Product.all_objects.filter(shop_id=shop.id).values_list("id", "cost_price"))
    for r in rows:
        total += (r["qty"] or ZERO) * (costs.get(r["product_id"]) or ZERO)
    return total


def branch_comparison(shop):
    """Side-by-side sales / profit / stock value per branch + % of total sales."""
    branches = list(Branch.objects.filter(shop=shop, is_active=True))
    rows = []
    grand_sales = ZERO
    for b in branches:
        items = SaleItem.all_objects.filter(shop_id=shop.id, sale__branch=b).exclude(
            sale__status=Sale.Status.CANCELLED
        )
        revenue = items.aggregate(
            v=Coalesce(Sum("subtotal", output_field=_DEC), ZERO, output_field=_DEC)
        )["v"]
        profit = items.aggregate(
            v=Coalesce(
                Sum(ExpressionWrapper(F("subtotal") - F("quantity") * F("unit_cost"), output_field=_DEC)),
                ZERO, output_field=_DEC,
            )
        )["v"]
        rows.append({
            "branch_id": b.id, "branch": b.name, "revenue": revenue,
            "profit": profit, "stock_value": branch_stock_value(shop, b),
        })
        grand_sales += revenue

    for r in rows:
        r["pct_of_total_sales"] = (
            round(float(r["revenue"] / grand_sales * 100), 2) if grand_sales else 0.0
        )
    return {"total_sales": grand_sales, "branches": rows}
