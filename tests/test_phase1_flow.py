"""End-to-end Phase 1: purchase -> stock -> sale -> COGS/profit -> dues."""
from decimal import Decimal

import pytest

from accounting.services import financial_position, profit_summary
from catalog.models import Product
from core.tenant_context import tenant_context
from crm.models import Customer
from inventory.models import MovementType, StockMovement
from inventory.services import apply_movement
from purchasing.models import Supplier
from purchasing.services import create_purchase_order, receive_purchase_order
from sales.services import add_payment, cancel_sale, create_sale

pytestmark = pytest.mark.django_db


@pytest.fixture
def shop_ctx(two_shops):
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        yield shop_a, owner_a


def _make_product(shop, cost="100", price="150", stock_tracked=True):
    return Product.objects.create(
        shop=shop, name="Widget", cost_price=Decimal(cost),
        selling_price=Decimal(price), track_inventory=stock_tracked,
        reorder_level=Decimal("5"),
    )


def test_receiving_po_increases_stock(shop_ctx):
    shop, owner = shop_ctx
    product = _make_product(shop)
    supplier = Supplier.objects.create(shop=shop, name="ACME Supply")

    po = create_purchase_order(
        shop=shop, supplier=supplier, created_by=owner,
        items=[{"product": product, "quantity": 10, "unit_cost": "90"}],
    )
    assert po.total == Decimal("900")

    receive_purchase_order(po=po, paid=Decimal("500"), created_by=owner)
    product.refresh_from_db()
    assert product.current_stock == Decimal("10")
    # cost refreshed to latest purchase price
    assert product.cost_price == Decimal("90")
    supplier.refresh_from_db()
    assert supplier.due_balance == Decimal("400")  # 900 - 500


def test_sale_moves_stock_and_snapshots_cogs(shop_ctx):
    shop, owner = shop_ctx
    product = _make_product(shop, cost="100", price="150")
    apply_movement(shop=shop, product=product, movement_type=MovementType.OPENING, quantity=20)

    customer = Customer.objects.create(shop=shop, name="Rahim")
    sale = create_sale(
        shop=shop, customer=customer, created_by=owner,
        items=[{"product": product, "quantity": 3, "unit_price": "150"}],
        payments=[{"amount": "300", "method": "cash"}],
    )

    assert sale.invoice_no.startswith("INV-")
    assert sale.total == Decimal("450")
    assert sale.paid == Decimal("300")
    assert sale.status == sale.Status.PARTIAL

    product.refresh_from_db()
    assert product.current_stock == Decimal("17")  # 20 - 3

    # COGS snapshot on the item even if product cost later changes
    item = sale.items.first()
    assert item.unit_cost == Decimal("100")
    product.cost_price = Decimal("999")
    product.save()
    assert item.cogs == Decimal("300")  # 3 * 100

    customer.refresh_from_db()
    assert customer.due_balance == Decimal("150")  # 450 - 300


def test_profit_is_transparent(shop_ctx):
    from accounting.models import Expense
    from django.utils import timezone

    shop, owner = shop_ctx
    product = _make_product(shop, cost="100", price="150")
    apply_movement(shop=shop, product=product, movement_type=MovementType.OPENING, quantity=50)

    create_sale(
        shop=shop, created_by=owner,
        items=[{"product": product, "quantity": 10, "unit_price": "150"}],
        payments=[{"amount": "1500"}],
    )
    Expense.objects.create(shop=shop, amount=Decimal("200"), spent_on=timezone.now().date())

    summary = profit_summary(shop)
    assert summary["revenue"] == Decimal("1500")
    assert summary["cogs"] == Decimal("1000")       # 10 * 100
    assert summary["gross_profit"] == Decimal("500")  # 1500 - 1000
    assert summary["expenses"] == Decimal("200")
    assert summary["net_profit"] == Decimal("300")    # 500 - 200


def test_add_payment_settles_due(shop_ctx):
    shop, owner = shop_ctx
    product = _make_product(shop)
    apply_movement(shop=shop, product=product, movement_type=MovementType.OPENING, quantity=10)
    customer = Customer.objects.create(shop=shop, name="Karim")

    sale = create_sale(
        shop=shop, customer=customer, created_by=owner,
        items=[{"product": product, "quantity": 2, "unit_price": "150"}],
    )
    assert sale.status == sale.Status.DUE
    add_payment(sale=sale, amount=Decimal("300"), created_by=owner)
    sale.refresh_from_db()
    assert sale.status == sale.Status.PAID
    customer.refresh_from_db()
    assert customer.due_balance == Decimal("0")


def test_cancel_sale_restocks_and_reverses(shop_ctx):
    shop, owner = shop_ctx
    product = _make_product(shop)
    apply_movement(shop=shop, product=product, movement_type=MovementType.OPENING, quantity=10)
    customer = Customer.objects.create(shop=shop, name="Jamal")

    sale = create_sale(
        shop=shop, customer=customer, created_by=owner,
        items=[{"product": product, "quantity": 4, "unit_price": "150"}],
        payments=[{"amount": "600"}],
    )
    product.refresh_from_db()
    assert product.current_stock == Decimal("6")

    cancel_sale(sale=sale, created_by=owner)
    product.refresh_from_db()
    assert product.current_stock == Decimal("10")  # restocked
    assert sale.status == sale.Status.CANCELLED


def test_stock_ledger_is_source_of_truth(shop_ctx):
    shop, owner = shop_ctx
    product = _make_product(shop)
    apply_movement(shop=shop, product=product, movement_type=MovementType.PURCHASE_IN, quantity=15)
    apply_movement(shop=shop, product=product, movement_type=MovementType.SALE_OUT, quantity=5)
    apply_movement(shop=shop, product=product, movement_type=MovementType.DAMAGE_OUT, quantity=2)
    # 15 - 5 - 2 = 8, recomputed purely from the ledger
    assert StockMovement.objects.filter(product=product).count() == 3
    product.refresh_from_db()
    assert product.current_stock == Decimal("8")
