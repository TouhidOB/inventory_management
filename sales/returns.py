"""
Returns & exchange service (8.4).

Returned stock goes back through the StockMovement ledger as ``return_in`` — a
distinct movement, never a silent stock bump. Refunds are manual/offline: we
record method + reference only, no gateway. Invoice status becomes
``partially_returned`` or ``returned`` based on quantities returned so far.
"""
from decimal import Decimal

from django.db import transaction

from audit.models import AuditLog
from audit.services import record
from inventory.models import MovementType
from inventory.services import apply_movement

from .models import Sale, SaleReturn, SaleReturnItem
from .services import ZERO, create_sale


def _returned_qty(sale_item):
    return sum(
        (ri.quantity for ri in sale_item.return_items.all()), ZERO
    )


@transaction.atomic
def create_return(
    *, sale, lines, reason="", refund_method=SaleReturn.RefundMethod.CASH,
    refund_reference="", restock=True, exchange_items=None, created_by=None,
):
    """
    ``lines``: list of {"sale_item": SaleItem, "quantity": Decimal}.
    ``exchange_items``: optional list in create_sale item format — if given, a
    new sale is created and the net (new sale total − refund) is returned.
    Returns (sale_return, exchange_sale_or_None, net_amount).
    net_amount > 0 => customer owes; < 0 => shop refunds.
    """
    if not lines:
        raise ValueError("A return needs at least one line.")

    shop = sale.shop
    sret = SaleReturn.objects.create(
        shop=shop, sale=sale, reason=reason,
        refund_method=refund_method, refund_reference=refund_reference,
        restocked=restock, created_by=created_by,
    )

    total_refund = ZERO
    for line in lines:
        item = line["sale_item"]
        qty = Decimal(line["quantity"])
        already = _returned_qty(item)
        if qty <= 0 or already + qty > item.quantity:
            raise ValueError(f"Invalid return quantity for item {item.id}.")

        refund_amount = qty * item.unit_price - _proportional_discount(item, qty)
        SaleReturnItem.objects.create(
            shop=shop, sale_return=sret, sale_item=item,
            quantity=qty, refund_amount=refund_amount,
        )
        total_refund += refund_amount

        if restock and item.product.track_inventory:
            apply_movement(
                shop=shop, product=item.product, variation=item.variation,
                movement_type=MovementType.SALE_RETURN_IN, quantity=qty,
                unit_cost=item.unit_cost, reference_type="SaleReturn",
                reference_id=sret.id, note="Return restock", created_by=created_by,
            )

    sret.total_refund = total_refund
    sret.save(update_fields=["total_refund"])

    _update_sale_return_status(sale)

    exchange_sale = None
    if exchange_items:
        exchange_sale = create_sale(
            shop=shop, customer=sale.customer, items=exchange_items,
            created_by=created_by, note=f"Exchange for {sale.invoice_no}",
        )
        sret.exchange_sale = exchange_sale
        sret.refund_method = SaleReturn.RefundMethod.EXCHANGE
        sret.save(update_fields=["exchange_sale", "refund_method"])

    net = (exchange_sale.total if exchange_sale else ZERO) - total_refund

    # Reduce customer receivable by the refunded amount (offline settlement).
    if sale.customer_id and total_refund:
        customer = sale.customer
        customer.due_balance = (customer.due_balance or ZERO) - total_refund
        customer.save(update_fields=["due_balance"])

    record(
        action=AuditLog.Action.UPDATE, actor=created_by, shop=shop, target=sret,
        description=f"Return on {sale.invoice_no} refund={total_refund} via {sret.refund_method}",
    )
    return sret, exchange_sale, net


def _proportional_discount(item, qty):
    """Discount attributable to the returned quantity."""
    if not item.quantity:
        return ZERO
    return (item.discount or ZERO) * (qty / item.quantity)


def _update_sale_return_status(sale):
    """Mark the sale returned / partially_returned based on quantities."""
    items = list(sale.items.prefetch_related("return_items").all())
    total_qty = sum((i.quantity for i in items), ZERO)
    returned_qty = sum((_returned_qty(i) for i in items), ZERO)
    if returned_qty <= 0:
        return
    if returned_qty >= total_qty:
        sale.status = Sale.Status.RETURNED
    else:
        sale.status = Sale.Status.PARTIALLY_RETURNED
    sale.save(update_fields=["status"])
