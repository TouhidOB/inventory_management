"""
Sales service layer — the transactional heart of the app.

``create_sale`` is atomic: it writes the invoice, snapshots costs, moves stock
through the ledger, records payments, updates the customer's due, and posts to
the cash ledger. If anything fails, nothing is committed.
"""
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from accounting.models import LedgerEntry
from audit.models import AuditLog
from audit.services import record
from inventory.models import MovementType
from inventory.services import apply_movement
from notifications.services import alert_low_stock_realtime

from .models import Payment, Sale, SaleItem

ZERO = Decimal("0")


def _next_invoice_no(shop) -> str:
    """Per-shop sequential invoice number. Prefix from shop invoice_settings."""
    prefix = (shop.invoice_settings or {}).get("invoice_prefix", "INV")
    count = Sale.all_objects.filter(shop_id=shop.id).count() + 1
    return f"{prefix}-{count:06d}"


@transaction.atomic
def create_sale(
    *, shop, items, customer=None, branch=None, discount=ZERO, tax=ZERO,
    payments=None, sale_date=None, note="", created_by=None,
    customer_name="", customer_phone="", customer_address="",
    idempotency_key="",
):
    """
    ``items``: list of dicts with keys ``product`` (instance), optional
    ``variation``, ``quantity``, ``unit_price``, optional ``discount``.
    ``payments``: list of dicts with ``amount`` and optional ``method``.
    ``idempotency_key``: optional client token; a repeat with the same key
    returns the already-created sale instead of making a duplicate.
    """
    if not items:
        raise ValueError("A sale needs at least one item.")

    # Idempotency: a rapid double-click / retry with the same key must not
    # create a second sale. Return the existing one if we've seen this key.
    if idempotency_key:
        existing = Sale.all_objects.filter(
            shop_id=shop.id, idempotency_key=idempotency_key
        ).first()
        if existing is not None:
            return existing

    # Lock the tracked products for the duration of this atomic transaction so
    # two concurrent checkouts of the same item can't BOTH pass the stock check
    # and oversell into negative (TOCTOU). The second sale blocks here until the
    # first commits, then sees the reduced stock and is rejected. On backends
    # without row locking (SQLite) this is a harmless no-op.
    from catalog.models import Product as _Product
    tracked_ids = [
        row["product"].id for row in items
        if getattr(row["product"], "track_inventory", False)
    ]
    locked_stock = {}
    if tracked_ids:
        locked_stock = {
            p.id: p.current_stock
            for p in _Product.all_objects.select_for_update().filter(id__in=tracked_ids)
        }

    # Block the sale if any tracked product lacks enough stock (item 7).
    for row in items:
        product = row["product"]
        if product.track_inventory:
            qty = Decimal(row["quantity"])
            # Use the freshly-locked stock, not the possibly-stale instance value.
            on_hand = locked_stock.get(product.id, product.current_stock)
            product.current_stock = on_hand
            if on_hand <= 0:
                raise ValueError(f"'{product.name}' is out of stock.")
            if qty > on_hand:
                raise ValueError(
                    f"Only {on_hand} of '{product.name}' in stock."
                )

    discount = Decimal(discount or 0)
    tax = Decimal(tax or 0)
    sale_date = sale_date or timezone.now()
    payments = payments or []

    # Generate the per-shop invoice number with retry: two concurrent sales can
    # compute the same count()+1, so the loser of the (shop, invoice_no) unique
    # constraint retries with a fresh number instead of 500-ing. Each attempt is
    # a savepoint inside the outer atomic block.
    common = dict(
        shop=shop, customer=customer, branch=branch, sale_date=sale_date,
        discount=discount, tax=tax, note=note, created_by=created_by,
        status=Sale.Status.DUE, idempotency_key=idempotency_key,
        # Clip walk-in receipt fields to their CharField limits (Postgres/MySQL
        # raise on overflow; SQLite silently truncates — keep both consistent).
        customer_name="" if customer is not None else (customer_name or "")[:150],
        customer_phone="" if customer is not None else (customer_phone or "")[:30],
        customer_address="" if customer is not None else customer_address,
    )
    sale = None
    for attempt in range(6):
        try:
            with transaction.atomic():
                sale = Sale.objects.create(invoice_no=_next_invoice_no(shop), **common)
            break
        except IntegrityError:
            # Another sale grabbed this invoice_no (or idempotency_key) first.
            if idempotency_key:
                dup = Sale.all_objects.filter(
                    shop_id=shop.id, idempotency_key=idempotency_key
                ).first()
                if dup is not None:
                    return dup
            if attempt == 5:
                raise


    subtotal = ZERO
    for row in items:
        product = row["product"]
        variation = row.get("variation")
        qty = Decimal(row["quantity"])
        unit_price = Decimal(row.get("unit_price", product.selling_price))
        line_discount = Decimal(row.get("discount", 0))
        # Snapshot cost for honest COGS.
        unit_cost = Decimal(
            variation.effective_cost if variation is not None else product.cost_price
        )

        item = SaleItem.objects.create(
            shop=shop, sale=sale, product=product, variation=variation,
            quantity=qty, unit_price=unit_price, unit_cost=unit_cost,
            discount=line_discount,
        )
        subtotal += item.subtotal

        if product.track_inventory:
            apply_movement(
                shop=shop, product=product, variation=variation,
                movement_type=MovementType.SALE_OUT, quantity=qty,
                unit_cost=unit_cost, branch=branch,
                reference_type="Sale", reference_id=sale.id,
                created_by=created_by,
            )

    # Customer default discount (percent of subtotal) if none supplied.
    if not discount and customer is not None and getattr(customer, "discount_percent", 0):
        discount = (subtotal * Decimal(customer.discount_percent) / Decimal("100")).quantize(Decimal("0.01"))
        sale.discount = discount

    # Apply the shop's VAT automatically when enabled and no explicit tax was
    # passed. VAT is charged on the post-discount amount and is a pass-through
    # liability — it inflates the invoice total (what the customer owes) but is
    # NOT revenue, so profit reporting excludes it.
    if not tax and getattr(shop, "vat_enabled", False) and getattr(shop, "vat_percent", 0):
        tax = ((subtotal - discount) * Decimal(shop.vat_percent) / Decimal("100")).quantize(Decimal("0.01"))
        sale.tax = tax

    total = subtotal - discount + tax
    paid = ZERO
    for p in payments:
        amount = Decimal(p["amount"])
        Payment.objects.create(
            shop=shop, sale=sale, amount=amount,
            method=p.get("method", Payment.Method.CASH),
        )
        paid += amount
        LedgerEntry.objects.create(
            shop=shop, account=LedgerEntry.Account.CASH, amount=amount,
            source_type="Sale", source_id=str(sale.id),
            description=f"Payment for {sale.invoice_no}",
        )

    sale.subtotal = subtotal
    sale.total = total
    sale.paid = paid
    sale.status = _resolve_status(total, paid)
    sale.save(update_fields=["subtotal", "total", "paid", "status", "discount", "tax"])

    if customer is not None:
        _update_customer_after_sale(customer, total=total, due=total - paid, when=sale_date)

    # Flip tracked ProductUnits (FIFO) to sold and bind the buyer onto each
    # unit's warranty, so a scanned unit resolves to who bought it + a valid
    # expiry on the warranty/return screen.
    _mark_units_sold(shop, sale)

    record(
        action=AuditLog.Action.CREATE, actor=created_by, shop=shop, target=sale,
        description=f"Sale {sale.invoice_no} total={total} paid={paid}",
    )

    # Real-time low-stock alert: a sale is the moment stock crosses the reorder
    # line. Fired only for shops on REALTIME mode; DAILY shops still get the
    # Celery digest. Runs after commit so we never alert on a rolled-back sale.
    sold_products = [row["product"] for row in items if row["product"].track_inventory]
    transaction.on_commit(
        lambda: alert_low_stock_realtime(shop=shop, products=sold_products)
    )
    return sale


def _mark_units_sold(shop, sale):
    """Start warranty coverage at the moment of sale.

    For each sold line: FIFO-flip in-stock ProductUnits to sold and bind the
    buyer + sale line onto their per-unit warranties (coverage starts on the
    sale date). If the product carries a warranty but not enough per-unit
    warranty records existed (e.g. it was bought by plain quantity without
    scanning barcodes), create the missing coverage now — so every warrantied
    item sold is recognized on the warranty list and claimable by serial.
    """
    from catalog.models import ProductUnit
    from service.models import Warranty

    today = timezone.localdate()
    for item in sale.items.select_related("product").all():
        product = item.product
        try:
            need = int(item.quantity)
        except (TypeError, ValueError):
            need = 0
        if need <= 0:
            continue

        # 1. Flip tracked physical units (FIFO) to sold.
        unit_ids = list(
            ProductUnit.all_objects.filter(
                shop_id=shop.id, product=product, status=ProductUnit.Status.IN_STOCK
            ).order_by("created_at").values_list("id", flat=True)[:need]
        )
        if unit_ids:
            ProductUnit.all_objects.filter(id__in=unit_ids).update(
                status=ProductUnit.Status.SOLD, sale=sale, sold_at=timezone.now())

        # 2. Start any existing per-unit warranties for those units.
        started = 0
        for w in Warranty.all_objects.filter(shop_id=shop.id, product_unit_id__in=unit_ids):
            w.customer = sale.customer
            w.sale_item = item
            w.start_date = today
            w.save(update_fields=["customer", "sale_item", "start_date", "expiry_date"])
            started += 1

        # 3. Fill the shortfall: create coverage for warrantied items that had
        #    no pre-existing per-unit warranty record.
        months = product.warranty_months or 0
        if months > 0 and started < need:
            for n in range(started + 1, need + 1):
                Warranty.all_objects.create(
                    shop_id=shop.id, product=product, customer=sale.customer,
                    sale_item=item, serial_no=f"{sale.invoice_no}-{item.id}-{n:03d}",
                    period_months=months, start_date=today,
                )


@transaction.atomic
def add_payment(*, sale, amount, method=Payment.Method.CASH, created_by=None):
    """Record an additional payment against a due/partial sale."""
    amount = Decimal(amount)
    if amount <= 0:
        raise ValueError("Payment amount must be positive.")
    Payment.objects.create(shop_id=sale.shop_id, sale=sale, amount=amount, method=method)
    LedgerEntry.objects.create(
        shop_id=sale.shop_id, account=LedgerEntry.Account.CASH, amount=amount,
        source_type="Sale", source_id=str(sale.id),
        description=f"Payment for {sale.invoice_no}",
    )
    sale.paid = (sale.paid or ZERO) + amount
    sale.status = _resolve_status(sale.total, sale.paid)
    sale.save(update_fields=["paid", "status"])
    if sale.customer_id:
        customer = sale.customer
        customer.due_balance = (customer.due_balance or ZERO) - amount
        customer.save(update_fields=["due_balance"])
    return sale


@transaction.atomic
def collect_customer_due(*, customer, amount, method=Payment.Method.CASH, created_by=None):
    """
    Collect a receivable from a customer, allocating the payment across their
    open invoices oldest-first (FIFO). Each allocation goes through
    ``add_payment`` so per-sale status, the cash ledger, and the customer's
    due balance all stay consistent. Returns the list of affected sales.

    Raises if the amount exceeds the customer's total outstanding due.
    """
    amount = Decimal(amount)
    if amount <= 0:
        raise ValueError("Amount must be positive.")

    open_sales = list(
        Sale.objects.filter(
            customer=customer,
            status__in=[Sale.Status.DUE, Sale.Status.PARTIAL, Sale.Status.PARTIALLY_RETURNED],
        ).order_by("sale_date", "id")
    )
    outstanding = sum((s.due for s in open_sales), ZERO)
    if amount > outstanding:
        raise ValueError(f"Amount exceeds outstanding due of {outstanding}.")

    affected, remaining = [], amount
    for sale in open_sales:
        if remaining <= 0:
            break
        portion = min(sale.due, remaining)
        if portion <= 0:
            continue
        add_payment(sale=sale, amount=portion, method=method, created_by=created_by)
        remaining -= portion
        affected.append(sale)
    return affected


@transaction.atomic
def cancel_sale(*, sale, created_by=None):
    """Void a sale: restock items, reverse cash + customer due, mark cancelled."""
    if sale.status == Sale.Status.CANCELLED:
        raise ValueError("Sale already cancelled.")

    for item in sale.items.select_related("product", "variation").all():
        if item.product.track_inventory:
            apply_movement(
                shop=sale.shop, product=item.product, variation=item.variation,
                movement_type=MovementType.SALE_RETURN_IN, quantity=item.quantity,
                unit_cost=item.unit_cost, reference_type="Sale",
                reference_id=sale.id, note="Sale cancelled", created_by=created_by,
            )

    if sale.paid:
        LedgerEntry.objects.create(
            shop_id=sale.shop_id, account=LedgerEntry.Account.CASH, amount=-sale.paid,
            source_type="Sale", source_id=str(sale.id),
            description=f"Refund/void {sale.invoice_no}",
        )
    if sale.customer_id:
        customer = sale.customer
        customer.due_balance = (customer.due_balance or ZERO) - sale.due
        customer.total_purchased = (customer.total_purchased or ZERO) - sale.total
        customer.save(update_fields=["due_balance", "total_purchased"])

    sale.status = Sale.Status.CANCELLED
    sale.save(update_fields=["status"])
    record(
        action=AuditLog.Action.DELETE, actor=created_by, shop=sale.shop, target=sale,
        description=f"Sale {sale.invoice_no} cancelled",
    )
    return sale


@transaction.atomic
def edit_sale(*, sale, items, discount=ZERO, created_by=None):
    """
    Edit a completed sale's line items + discount (item 12). Ledger-safe:
    reverses the old lines with SALE_RETURN_IN, deletes old SaleItems, then
    re-sells the new lines with SALE_OUT — keeping the same invoice number.
    Payments are untouched; the due is recomputed. Blocks cancelled/returned.
    """
    if sale.status in (Sale.Status.CANCELLED, Sale.Status.RETURNED, Sale.Status.PARTIALLY_RETURNED):
        raise ValueError("This sale can no longer be edited.")
    if not items:
        raise ValueError("A sale needs at least one item.")

    old_total = sale.total or ZERO
    paid = sale.paid or ZERO

    # 1. Reverse old stock, then drop the old lines.
    for item in sale.items.select_related("product", "variation").all():
        if item.product.track_inventory:
            apply_movement(
                shop=sale.shop, product=item.product, variation=item.variation,
                movement_type=MovementType.SALE_RETURN_IN, quantity=item.quantity,
                unit_cost=item.unit_cost, reference_type="Sale",
                reference_id=sale.id, note="Edit: reverse", created_by=created_by,
            )
    sale.items.all().delete()

    # 2. Validate stock (post-reversal) + re-sell the new lines.
    for row in items:
        product = row["product"]
        if product.track_inventory:
            product.refresh_from_db(fields=["current_stock"])
            qty = Decimal(row["quantity"])
            if qty > product.current_stock:
                raise ValueError(f"Only {product.current_stock} of '{product.name}' in stock.")

    discount = Decimal(discount or 0)
    subtotal = ZERO
    for row in items:
        product = row["product"]
        variation = row.get("variation")
        qty = Decimal(row["quantity"])
        unit_price = Decimal(row.get("unit_price", product.selling_price))
        unit_cost = Decimal(variation.effective_cost if variation is not None else product.cost_price)
        item = SaleItem.objects.create(
            shop=sale.shop, sale=sale, product=product, variation=variation,
            quantity=qty, unit_price=unit_price, unit_cost=unit_cost,
        )
        subtotal += item.subtotal
        if product.track_inventory:
            apply_movement(
                shop=sale.shop, product=product, variation=variation,
                movement_type=MovementType.SALE_OUT, quantity=qty, unit_cost=unit_cost,
                reference_type="Sale", reference_id=sale.id, note="Edit: re-sell",
                created_by=created_by,
            )

    total = subtotal - discount + (sale.tax or ZERO)
    sale.subtotal = subtotal
    sale.discount = discount
    sale.total = total
    sale.status = _resolve_status(total, paid)
    # Include updated_at so the optimistic-lock token (web.sale_edit compares the
    # sale's updated_at) actually advances on every edit — otherwise a concurrent
    # stale save would not be detected. auto_now fields aren't bumped when left
    # out of update_fields.
    sale.save(update_fields=["subtotal", "discount", "total", "status", "updated_at"])

    # 3. Adjust the customer's cached due + total by the deltas.
    if sale.customer_id:
        customer = sale.customer
        new_due, old_due = total - paid, old_total - paid
        customer.due_balance = (customer.due_balance or ZERO) + (new_due - old_due)
        customer.total_purchased = (customer.total_purchased or ZERO) + (total - old_total)
        customer.save(update_fields=["due_balance", "total_purchased"])

    record(
        action=AuditLog.Action.UPDATE, actor=created_by, shop=sale.shop, target=sale,
        description=f"Sale {sale.invoice_no} edited: total {old_total} → {total}",
    )
    return sale


def _resolve_status(total, paid):
    if paid >= total and total > 0:
        return Sale.Status.PAID
    if paid <= 0:
        return Sale.Status.DUE
    return Sale.Status.PARTIAL


def _update_customer_after_sale(customer, *, total, due, when):
    customer.total_purchased = (customer.total_purchased or ZERO) + total
    customer.due_balance = (customer.due_balance or ZERO) + due
    customer.last_purchase_at = when
    customer.save(update_fields=["total_purchased", "due_balance", "last_purchase_at"])
