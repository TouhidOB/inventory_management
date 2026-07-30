"""Purchasing service layer: build a PO, then receive it into stock."""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from accounting.models import ExpenseCategory, LedgerEntry
from accounting.services import record_expense
from inventory.models import MovementType
from inventory.services import apply_movement

from .models import PurchaseOrder, PurchaseOrderItem, PurchasePayment, SupplierPayment

ZERO = Decimal("0")

# Purchase payments are booked to this expense category so they show up in the
# shop's accounting / expense views.
PURCHASE_EXPENSE_CATEGORY = "Product Purchase"


def _purchase_expense_category(shop):
    cat, _ = ExpenseCategory.all_objects.get_or_create(
        shop_id=shop.id, name=PURCHASE_EXPENSE_CATEGORY
    )
    return cat


def post_purchase_payment_to_accounting(*, shop, po, amount, when=None, created_by=None):
    """
    Decide whether a product-purchase transaction is an expense or income,
    then post it to the accounting tables (feature #3).

    * amount > 0  → cash paid to supplier ⇒ EXPENSE: an ``Expense`` row (under
      the "Product Purchase" category) plus a cash-out ``LedgerEntry`` — both
      created by ``record_expense``.
    * amount < 0  → refund/return from supplier ⇒ INCOME: a positive cash
      ``LedgerEntry`` only (the Expense model represents money out).

    Returns the created ``Expense`` for the expense path, else ``None``.
    """
    from django.utils import timezone

    amount = Decimal(amount)
    if amount == 0:
        return None
    if amount > 0:
        return record_expense(
            shop=shop, amount=amount, spent_on=when or timezone.localdate(),
            category=_purchase_expense_category(shop), payment_method="",
            note=f"Purchase payment {po.po_number}", created_by=created_by,
        )
    LedgerEntry.all_objects.create(
        shop_id=shop.id, account=LedgerEntry.Account.CASH, amount=-amount,
        source_type="PurchaseRefund", source_id=str(po.id),
        description=f"Purchase refund {po.po_number}",
    )
    return None


@transaction.atomic
def add_purchase_payment(*, po, amount, method=PurchasePayment.Method.CASH,
                         reference="", note="", created_by=None):
    """
    Record a partial (or full) payment against a purchase order. Bumps
    ``po.paid``, lowers the supplier's cached ``due_balance`` and posts the
    payment to accounting. Rejects amounts above the outstanding PO due.
    """
    amount = Decimal(amount)
    if amount <= 0:
        raise ValueError("Amount must be positive.")
    due = (po.total or ZERO) - (po.paid or ZERO)
    if amount > due:
        raise ValueError(f"Amount exceeds PO due of {due}.")

    payment = PurchasePayment.objects.create(
        shop_id=po.shop_id, purchase_order=po, amount=amount, method=method,
        reference=reference, note=note, created_by=created_by,
    )
    po.paid = (po.paid or ZERO) + amount
    po.save(update_fields=["paid"])

    supplier = po.supplier
    supplier.due_balance = (supplier.due_balance or ZERO) - amount
    supplier.save(update_fields=["due_balance"])

    post_purchase_payment_to_accounting(
        shop=po.shop, po=po, amount=amount, created_by=created_by,
    )
    return payment


@transaction.atomic
def pay_supplier(*, supplier, amount, method=SupplierPayment.Method.CASH,
                 reference="", note="", created_by=None):
    """
    Pay down a supplier's outstanding payable. Records a SupplierPayment,
    reduces the supplier's cached ``due_balance``, and writes a cash outflow.
    Raises if the amount exceeds what is currently owed.
    """
    amount = Decimal(amount)
    if amount <= 0:
        raise ValueError("Amount must be positive.")
    outstanding = supplier.due_balance or ZERO
    if amount > outstanding:
        raise ValueError(f"Amount exceeds outstanding due of {outstanding}.")

    payment = SupplierPayment.objects.create(
        shop_id=supplier.shop_id, supplier=supplier, amount=amount,
        method=method, reference=reference, note=note, created_by=created_by,
    )
    supplier.due_balance = outstanding - amount
    supplier.save(update_fields=["due_balance"])
    LedgerEntry.objects.create(
        shop_id=supplier.shop_id, account=LedgerEntry.Account.CASH, amount=-amount,
        source_type="SupplierPayment", source_id=str(payment.id),
        description=f"Payment to {supplier.name}",
    )
    return payment


def _materialize_units_with_warranty(po, item):
    """For a received PO line, create one ``ProductUnit`` (with a generated
    serial barcode) and one per-unit ``Warranty`` per piece, when the product
    offers a warranty. Lets a bulk-bought batch be returned one unit at a time.
    Skips products with no warranty period so non-serialized stock is untouched.
    """
    from django.utils import timezone as _tz

    from catalog.models import ProductUnit
    from service.models import Warranty

    product = item.product
    months = getattr(product, "warranty_months", 0) or 0
    if months <= 0:
        return
    qty = int(item.quantity or 0)
    if qty <= 0:
        return
    start = _tz.localdate()
    for n in range(1, qty + 1):
        serial = f"{po.po_number}-{item.id}-{n:03d}"
        unit = ProductUnit.all_objects.create(
            shop_id=po.shop_id, product=product, barcode=serial,
            status=ProductUnit.Status.IN_STOCK,
            cost_price=item.unit_cost, selling_price=product.selling_price,
            warranty_months=months,
        )
        Warranty.all_objects.create(
            shop_id=po.shop_id, product=product, product_unit=unit,
            serial_no=serial, period_months=months, start_date=start,
        )


def _next_po_number(shop) -> str:
    count = PurchaseOrder.all_objects.filter(shop_id=shop.id).count() + 1
    return f"PO-{count:06d}"


@transaction.atomic
def create_purchase_order(
    *, shop, supplier, items, branch=None, discount=ZERO, order_date=None,
    note="", created_by=None,
):
    """``items``: dicts with ``product``, optional ``variation``, ``quantity``,
    ``unit_cost``."""
    po = PurchaseOrder.objects.create(
        shop=shop, supplier=supplier, branch=branch,
        po_number=_next_po_number(shop),
        status=PurchaseOrder.Status.ORDERED,
        order_date=order_date or timezone.now().date(),
        discount=Decimal(discount or 0), note=note, created_by=created_by,
    )
    subtotal = ZERO
    for row in items:
        item = PurchaseOrderItem.objects.create(
            shop=shop, purchase_order=po, product=row["product"],
            variation=row.get("variation"),
            quantity=Decimal(row["quantity"]), unit_cost=Decimal(row["unit_cost"]),
        )
        subtotal += item.subtotal

    po.subtotal = subtotal
    po.total = subtotal - po.discount
    po.save(update_fields=["subtotal", "total"])
    return po


@transaction.atomic
def receive_purchase_order(*, po, paid=ZERO, update_cost=True, created_by=None):
    """
    Receive an ordered PO into stock: write PURCHASE_IN movements for each line,
    optionally refresh product cost, record supplier due + any payment.
    """
    if po.status == PurchaseOrder.Status.RECEIVED:
        raise ValueError("Purchase order already received.")

    for item in po.items.select_related("product", "variation").all():
        apply_movement(
            shop=po.shop, product=item.product, variation=item.variation,
            movement_type=MovementType.PURCHASE_IN, quantity=item.quantity,
            unit_cost=item.unit_cost, branch=po.branch,
            reference_type="PurchaseOrder", reference_id=po.id, created_by=created_by,
        )
        if update_cost:
            # Latest purchase cost becomes the product's standard cost.
            product = item.product
            product.cost_price = item.unit_cost
            product.save(update_fields=["cost_price"])
        # Bulk purchase → per-unit warranty tracking: one ProductUnit + Warranty
        # per received piece, so the batch is bought together but returned one
        # unit at a time.
        _materialize_units_with_warranty(po, item)

    paid = Decimal(paid or 0)
    po.paid = paid
    po.status = PurchaseOrder.Status.RECEIVED
    po.received_at = timezone.now()
    po.save(update_fields=["paid", "status", "received_at"])

    # Supplier owed the unpaid remainder; record any cash outflow.
    supplier = po.supplier
    supplier.due_balance = (supplier.due_balance or ZERO) + (po.total - paid)
    supplier.save(update_fields=["due_balance"])

    if paid > 0:
        # An initial payment at receive is a product-purchase payment too:
        # classify it and post to accounting (Expense row + cash ledger).
        post_purchase_payment_to_accounting(
            shop=po.shop, po=po, amount=paid, created_by=created_by,
        )
    return po
