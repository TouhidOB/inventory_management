"""
Concurrency / misuse regression tests.

The oversell guard in ``sales.services.create_sale`` uses ``select_for_update``
so two simultaneous checkouts of the last unit can't both pass the stock check.
On PostgreSQL this is enforced by real row locking; on SQLite the lock is a
no-op but writes serialize, so the invariant (never oversell into negative)
holds either way. These tests assert that invariant under thread contention and
would catch a regression that dropped the lock.
"""
import threading
from decimal import Decimal as D

import pytest
from django.db import connection

from catalog.models import Product
from core.tenant_context import bypass_tenant_scope, set_current_tenant
from crm.models import Customer
from inventory.services import restock
from sales.services import create_sale
from tenants.models import Shop

# SQLite serialises writers with a whole-database lock (concurrent writers get
# "database is locked" rather than real row-level contention), so select_for_update
# can't be exercised there. These tests validate the oversell guard under genuine
# row locking — run them on PostgreSQL (CI / staging).
pytestmark = pytest.mark.skipif(
    connection.vendor == "sqlite",
    reason="Concurrency/row-lock behaviour is only meaningful on PostgreSQL.",
)


@pytest.mark.django_db(transaction=True)
def test_concurrent_checkout_never_oversells():
    with bypass_tenant_scope():
        shop = Shop.objects.create(name="ConcurrencyCo")
    set_current_tenant(shop)
    product = Product.objects.create(
        shop=shop, name="LastUnit", cost_price=D("10"), selling_price=D("20"),
        track_inventory=True,
    )
    restock(product=product, quantity=D("1"), unit_cost=D("10"))  # exactly ONE in stock
    customer = Customer.objects.create(shop=shop, name="Buyer", phone="x1")

    successes, failures = [], []
    barrier = threading.Barrier(5)

    def buy():
        set_current_tenant(shop)
        barrier.wait()  # release all threads together to maximise contention
        try:
            create_sale(
                shop=shop, customer=customer,
                items=[{"product": product, "quantity": D("1"), "unit_price": D("20")}],
                payments=[{"amount": D("20")}],
            )
            successes.append(1)
        except ValueError:
            failures.append(1)          # "out of stock" — expected for the losers
        finally:
            connection.close()          # each thread owns its connection

    threads = [threading.Thread(target=buy) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    product.refresh_from_db()
    # Exactly one sale for the one unit; the rest rejected; stock never negative.
    assert len(successes) == 1, f"oversold: {len(successes)} sales for 1 unit"
    assert len(failures) == 4
    assert product.current_stock == D("0"), f"stock went to {product.current_stock}"


@pytest.mark.django_db(transaction=True)
def test_concurrent_checkout_respects_available_quantity():
    """With N units and M>N buyers, exactly N sales succeed and stock hits 0."""
    with bypass_tenant_scope():
        shop = Shop.objects.create(name="ConcurrencyCo2")
    set_current_tenant(shop)
    product = Product.objects.create(
        shop=shop, name="ThreeUnits", cost_price=D("5"), selling_price=D("10"),
        track_inventory=True,
    )
    restock(product=product, quantity=D("3"), unit_cost=D("5"))
    customer = Customer.objects.create(shop=shop, name="B", phone="x2")

    ok = []
    barrier = threading.Barrier(6)

    def buy():
        set_current_tenant(shop)
        barrier.wait()
        try:
            create_sale(
                shop=shop, customer=customer,
                items=[{"product": product, "quantity": D("1"), "unit_price": D("10")}],
                payments=[{"amount": D("10")}],
            )
            ok.append(1)
        except ValueError:
            pass
        finally:
            connection.close()

    threads = [threading.Thread(target=buy) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    product.refresh_from_db()
    assert len(ok) == 3, f"expected 3 sales, got {len(ok)}"
    assert product.current_stock == D("0")
