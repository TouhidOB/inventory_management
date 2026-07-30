"""Due management: customer receivables (FIFO collection) + supplier payables."""
from decimal import Decimal

import pytest

from accounting.models import LedgerEntry
from catalog.models import Product
from core.tenant_context import tenant_context
from crm.models import Customer
from inventory.models import MovementType
from inventory.services import apply_movement
from accounting.models import Expense
from purchasing.models import PurchasePayment, Supplier, SupplierPayment
from purchasing.services import add_purchase_payment, create_purchase_order, pay_supplier, receive_purchase_order
from sales.models import Sale
from sales.services import collect_customer_due, create_sale

pytestmark = pytest.mark.django_db


@pytest.fixture
def shop_ctx(two_shops):
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        yield shop_a, owner_a


def _product(shop, price="100"):
    p = Product.objects.create(shop=shop, name="Item", cost_price=Decimal("50"),
                               selling_price=Decimal(price))
    apply_movement(shop=shop, product=p, movement_type=MovementType.OPENING, quantity=100)
    return p


def test_collect_customer_due_fifo_across_invoices(shop_ctx):
    shop, owner = shop_ctx
    p = _product(shop)
    cust = Customer.objects.create(shop=shop, name="Debtor", phone="01")
    # Two unpaid sales of 100 each -> due 200.
    s1 = create_sale(shop=shop, customer=cust, created_by=owner,
                     items=[{"product": p, "quantity": 1, "unit_price": 100}])
    s2 = create_sale(shop=shop, customer=cust, created_by=owner,
                     items=[{"product": p, "quantity": 1, "unit_price": 100}])
    cust.refresh_from_db()
    assert cust.due_balance == Decimal("200")

    # Collect 150 -> clears s1 fully, s2 partially.
    collect_customer_due(customer=cust, amount=Decimal("150"), created_by=owner)
    cust.refresh_from_db(); s1.refresh_from_db(); s2.refresh_from_db()
    assert cust.due_balance == Decimal("50")
    assert s1.status == Sale.Status.PAID
    assert s2.status == Sale.Status.PARTIAL and s2.paid == Decimal("50")


def test_collect_more_than_due_rejected(shop_ctx):
    shop, owner = shop_ctx
    p = _product(shop)
    cust = Customer.objects.create(shop=shop, name="D2", phone="02")
    create_sale(shop=shop, customer=cust, created_by=owner,
                items=[{"product": p, "quantity": 1, "unit_price": 100}])
    with pytest.raises(ValueError):
        collect_customer_due(customer=cust, amount=Decimal("500"), created_by=owner)


def test_pay_supplier_reduces_due_and_writes_ledger(shop_ctx):
    shop, owner = shop_ctx
    sup = Supplier.objects.create(shop=shop, name="Vendor", due_balance=Decimal("300"))
    pay_supplier(supplier=sup, amount=Decimal("120"), method="bkash",
                 reference="TXN9", created_by=owner)
    sup.refresh_from_db()
    assert sup.due_balance == Decimal("180")
    assert SupplierPayment.all_objects.filter(shop_id=shop.id, supplier=sup).count() == 1
    entry = LedgerEntry.all_objects.get(shop_id=shop.id, source_type="SupplierPayment")
    assert entry.amount == Decimal("-120")  # cash outflow


def test_pay_supplier_over_due_rejected(shop_ctx):
    shop, owner = shop_ctx
    sup = Supplier.objects.create(shop=shop, name="V2", due_balance=Decimal("50"))
    with pytest.raises(ValueError):
        pay_supplier(supplier=sup, amount=Decimal("80"), created_by=owner)


def test_partial_purchase_payments_reduce_due_and_book_expense(shop_ctx):
    """Feature #2 + #3: partial PO payments accumulate, lower the PO due and the
    supplier payable, and post an Expense row (+ cash ledger) to accounting."""
    shop, owner = shop_ctx
    p = _product(shop)
    sup = Supplier.objects.create(shop=shop, name="Acme")
    po = create_purchase_order(
        shop=shop, supplier=sup, created_by=owner,
        items=[{"product": p, "quantity": 10, "unit_cost": "90"}],
    )
    # Receive with a partial initial payment; supplier owes the remainder.
    receive_purchase_order(po=po, paid=Decimal("300"), created_by=owner)
    po.refresh_from_db(); sup.refresh_from_db()
    assert po.due == Decimal("600")            # 900 total - 300 paid
    assert sup.due_balance == Decimal("600")

    # A later partial payment reduces the PO due and the supplier payable.
    add_purchase_payment(po=po, amount=Decimal("200"), method="bkash", created_by=owner)
    po.refresh_from_db(); sup.refresh_from_db()
    assert po.paid == Decimal("500")
    assert po.due == Decimal("400")
    assert sup.due_balance == Decimal("400")
    assert PurchasePayment.all_objects.filter(shop_id=shop.id, purchase_order=po).count() == 1

    # Both payments booked as "Product Purchase" expenses + cash-out ledger.
    purchases_expense = Expense.all_objects.filter(
        shop_id=shop.id, category__name="Product Purchase"
    )
    assert purchases_expense.count() == 2
    ledger_out = LedgerEntry.all_objects.filter(
        shop_id=shop.id, source_type="Expense", amount__lt=0
    )
    assert ledger_out.count() == 2


def test_bulk_receive_creates_per_unit_warranties(shop_ctx):
    """Feature: bulk purchase → one ProductUnit + Warranty per received piece,
    so the batch is bought together but returned one unit at a time."""
    from catalog.models import ProductUnit
    from service.models import Warranty

    shop, owner = shop_ctx
    p = Product.objects.create(shop=shop, name="Gadget", cost_price=Decimal("50"),
                               selling_price=Decimal("100"), warranty_months=12)
    sup = Supplier.objects.create(shop=shop, name="Vend")
    po = create_purchase_order(
        shop=shop, supplier=sup, created_by=owner,
        items=[{"product": p, "quantity": 3, "unit_cost": "50"}],
    )
    receive_purchase_order(po=po, paid=Decimal("0"), created_by=owner)

    units = ProductUnit.all_objects.filter(shop_id=shop.id, product=p)
    warranties = Warranty.all_objects.filter(shop_id=shop.id, product=p)
    assert units.count() == 3
    assert warranties.count() == 3
    # Every warranty is bound to a distinct unit with a serial.
    assert warranties.filter(product_unit__isnull=False).count() == 3
    assert all(w.serial_no for w in warranties)


def test_purchase_payment_over_due_rejected(shop_ctx):
    shop, owner = shop_ctx
    p = _product(shop)
    sup = Supplier.objects.create(shop=shop, name="Acme2")
    po = create_purchase_order(
        shop=shop, supplier=sup, created_by=owner,
        items=[{"product": p, "quantity": 1, "unit_cost": "100"}],
    )
    receive_purchase_order(po=po, paid=Decimal("0"), created_by=owner)
    with pytest.raises(ValueError):
        add_purchase_payment(po=po, amount=Decimal("500"), created_by=owner)


def test_dues_page_loads_and_collects(client, two_shops):
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        p = _product(shop_a)
        cust = Customer.objects.create(shop=shop_a, name="Pagey", phone="09")
        create_sale(shop=shop_a, customer=cust, created_by=owner_a,
                    items=[{"product": p, "quantity": 1, "unit_price": 100}])
    assert client.login(email=owner_a.email, password="pass12345")
    assert client.get("/dues/").status_code == 200
    resp = client.post("/dues/", {"action": "collect", "customer_id": cust.id,
                                  "amount": "100", "method": "cash"})
    assert resp.status_code == 302
    with tenant_context(shop_a):
        cust.refresh_from_db()
        assert cust.due_balance == Decimal("0")
