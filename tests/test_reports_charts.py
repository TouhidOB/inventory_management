"""Reports-page analytics charts: repair-shop aggregations feeding Chart.js."""
from decimal import Decimal

import pytest

from analytics import services as A
from catalog.models import Category, Product
from core.tenant_context import tenant_context
from crm.models import Customer
from inventory.models import MovementType
from inventory.services import apply_movement
from sales.services import create_sale
from service.models import ServiceTicket
from service.services import change_ticket_status, create_ticket

pytestmark = pytest.mark.django_db


@pytest.fixture
def shop_ctx(two_shops):
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        yield shop_a, owner_a


def _product(shop, name="SSD", cost="1000", price="1500", cat=None):
    return Product.objects.create(
        shop=shop, name=name, cost_price=Decimal(cost), selling_price=Decimal(price),
        reorder_level=Decimal("5"), category=cat,
    )


def _seed(shop, owner):
    p = _product(shop, "SSD 1TB")
    apply_movement(shop=shop, product=p, movement_type=MovementType.OPENING, quantity=20)
    cust = Customer.objects.create(shop=shop, name="Ali", phone="0170")
    # Two sales for same customer -> one new + one returning month bucket at least.
    create_sale(shop=shop, customer=cust, created_by=owner,
                items=[{"product": p, "quantity": 2, "unit_price": "1500"}])
    create_sale(shop=shop, customer=cust, created_by=owner,
                items=[{"product": p, "quantity": 1, "unit_price": "1500"}])
    # A delivered repair ticket with structured taxonomy.
    t = create_ticket(shop=shop, customer=cust, device_description="Dell XPS",
                      device_type=ServiceTicket.DeviceType.LAPTOP,
                      issue_type=ServiceTicket.IssueType.SCREEN,
                      complaint="cracked", service_charge=Decimal("500"), created_by=owner)
    change_ticket_status(ticket=t, new_status=ServiceTicket.Status.DELIVERED, changed_by=owner)
    return p, cust


def test_reports_charts_bundle_shape(shop_ctx):
    shop, owner = shop_ctx
    _seed(shop, owner)
    c = A.reports_charts(shop)

    # Every requested chart present.
    assert set(c) >= {
        "revenue_trend", "sales_mix", "top_parts", "tat", "device_volume",
        "issue_pareto", "stock_turnover", "reorder", "acquisition",
    }
    # Revenue split has parallel parts/labor series.
    rt = c["revenue_trend"]
    assert len(rt["labels"]) == len(rt["parts"]) == len(rt["labor"]) == 6
    assert sum(rt["parts"]) > 0 and sum(rt["labor"]) > 0  # sales + service charge
    # Sales mix three slices, parts revenue registered.
    assert c["sales_mix"]["labels"][0] == "Hardware parts"
    assert c["sales_mix"]["values"][0] > 0
    # Delivered ticket counted in TAT stats.
    assert c["tat"]["stats"]["count"] == 1
    # Device volume has a Laptop dataset.
    assert any(d["label"] == "Laptop" for d in c["device_volume"]["datasets"])
    # Issue Pareto cumulative ends at 100%.
    assert c["issue_pareto"]["cumulative"][-1] == 100.0
    # Stock turnover point carries margin + label.
    pts = c["stock_turnover"]["points"]
    assert pts and pts[0]["y"] == pytest.approx(33.3, abs=0.2)  # (1500-1000)/1500
    # Reorder row exposes current vs threshold.
    assert c["reorder"][0]["threshold"] == 5.0


def test_reports_charts_empty_shop_no_crash(shop_ctx):
    shop, _owner = shop_ctx
    c = A.reports_charts(shop)
    assert c["tat"]["stats"]["count"] == 0
    assert c["stock_turnover"]["points"] == []
    assert c["issue_pareto"]["counts"] == []


def test_charts_are_tenant_scoped(two_shops):
    (shop_a, owner_a), (shop_b, owner_b) = two_shops
    with tenant_context(shop_a):
        _seed(shop_a, owner_a)
    # Shop B is empty; its charts must not leak shop A's data.
    with tenant_context(shop_b):
        c = A.reports_charts(shop_b)
    assert c["tat"]["stats"]["count"] == 0
    assert c["sales_mix"]["values"] == [0.0, 0.0, 0.0]
