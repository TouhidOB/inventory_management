"""Stock ledger service layer — the only sanctioned way to move stock."""
from decimal import Decimal

from django.db import transaction
from django.db.models import F, Sum

from catalog.models import Product, ProductVariation

from .models import OUTGOING_TYPES, StockMovement


def _signed(movement_type, quantity) -> Decimal:
    """Return a signed quantity: negative for outgoing movement types."""
    qty = abs(Decimal(quantity))
    return -qty if movement_type in OUTGOING_TYPES else qty


@transaction.atomic
def apply_movement(
    *, product, movement_type, quantity, unit_cost=0, variation=None,
    branch=None, reference_type="", reference_id="", note="", created_by=None,
    shop=None,
):
    """
    Record a stock movement and update the cached ``current_stock``.

    ``quantity`` may be passed as a positive magnitude; the sign is derived
    from ``movement_type``. Returns the created StockMovement.
    """
    shop = shop or product.shop
    signed = _signed(movement_type, quantity)

    movement = StockMovement.objects.create(
        shop=shop, product=product, variation=variation, branch=branch,
        movement_type=movement_type, quantity=signed, unit_cost=unit_cost or 0,
        reference_type=reference_type, reference_id=str(reference_id),
        note=note, created_by=created_by,
    )
    _apply_stock_delta(product, variation, signed)
    return movement


def _apply_stock_delta(product, variation, signed):
    """Adjust the cached ``current_stock`` by a single movement in O(1).

    Movements are immutable and append-only, so the cache can be nudged by the
    delta instead of re-summing the whole ledger on every sale/purchase line
    (which was O(movements-per-product) and got slower as history grew). The
    ``F()`` expression is evaluated in the database, so concurrent movements
    increment atomically with no read-modify-write race. ``recalc_stock`` remains
    the authoritative reconcile (management command / periodic task)."""
    signed = Decimal(signed)
    Product.all_objects.filter(pk=product.pk).update(
        current_stock=F("current_stock") + signed
    )
    # Keep the in-memory instance consistent for callers that read it right after
    # (weighted-avg cost in restock, low-stock alert).
    product.current_stock = Decimal(product.current_stock or 0) + signed

    if variation is not None:
        ProductVariation.all_objects.filter(pk=variation.pk).update(
            current_stock=F("current_stock") + signed
        )
        variation.current_stock = Decimal(variation.current_stock or 0) + signed

    from analytics.services import invalidate_dashboard_cache
    invalidate_dashboard_cache(product.shop_id)


def recalc_stock(product, variation=None):
    """Authoritative reconcile: recompute cached stock for a product (and
    variation) from the full ledger. Used by the reconcile command / periodic
    task and as a repair hook; the hot write path uses ``_apply_stock_delta``."""
    # Product-level total (all movements for the product, any variation).
    total = (
        StockMovement.all_objects.filter(shop_id=product.shop_id, product=product)
        .aggregate(s=Sum("quantity"))["s"] or Decimal("0")
    )
    Product.all_objects.filter(pk=product.pk).update(current_stock=total)
    product.current_stock = total

    if variation is not None:
        vtotal = (
            StockMovement.all_objects.filter(
                shop_id=product.shop_id, variation=variation
            ).aggregate(s=Sum("quantity"))["s"] or Decimal("0")
        )
        ProductVariation.all_objects.filter(pk=variation.pk).update(current_stock=vtotal)
        variation.current_stock = vtotal

    # Keep cached dashboards fresh after any stock change.
    from analytics.services import invalidate_dashboard_cache
    invalidate_dashboard_cache(product.shop_id)
    return total


@transaction.atomic
def restock(*, product, quantity, unit_cost, note="", created_by=None, branch=None):
    """
    Re-inventory a product: add stock at a (possibly different) purchase price
    and roll the product's cost into a weighted average so margins stay honest
    when the buying price changes (item 6). Returns a dict with old/new cost.
    """
    quantity = Decimal(quantity)
    unit_cost = Decimal(unit_cost)
    if quantity <= 0:
        raise ValueError("Restock quantity must be positive.")

    old_cost = Decimal(product.cost_price or 0)
    on_hand = Decimal(product.current_stock or 0)
    on_hand_pos = on_hand if on_hand > 0 else Decimal("0")
    denom = on_hand_pos + quantity
    new_cost = ((on_hand_pos * old_cost + quantity * unit_cost) / denom).quantize(Decimal("0.01")) if denom else unit_cost

    apply_movement(
        product=product, movement_type="purchase_in", quantity=quantity,
        unit_cost=unit_cost, note=note or "Restock", created_by=created_by, branch=branch,
    )
    product.cost_price = new_cost
    product.save(update_fields=["cost_price"])
    return {"old_cost": old_cost, "new_purchase_cost": unit_cost,
            "new_avg_cost": new_cost, "price_difference": unit_cost - old_cost}


def low_stock_products(shop):
    """Products at or below their reorder level (tenant-scoped)."""
    return Product.objects.filter(
        shop=shop, track_inventory=True, is_active=True,
        current_stock__lte=F("reorder_level"),
    )
