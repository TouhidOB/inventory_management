"""API-layer Phase 1 checks incl. cross-tenant isolation on real resources."""
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from catalog.models import Product
from core.tenant_context import tenant_context
from inventory.models import MovementType
from inventory.services import apply_movement

pytestmark = pytest.mark.django_db


@pytest.fixture
def api():
    return APIClient()


def _product(shop, name):
    with tenant_context(shop):
        return Product.objects.create(
            shop=shop, name=name, cost_price=Decimal("100"),
            selling_price=Decimal("150"), reorder_level=Decimal("5"),
        )


def test_products_are_tenant_isolated_over_api(api, two_shops):
    (shop_a, owner_a), (shop_b, owner_b) = two_shops
    _product(shop_a, "A-Widget")
    _product(shop_b, "B-Gadget")

    api.force_authenticate(owner_a)
    resp = api.get("/api/catalog/products/")
    assert resp.status_code == 200
    names = {p["name"] for p in resp.data["results"]}
    assert names == {"A-Widget"}  # never sees shop B's product


def test_pos_lookup_and_checkout(api, two_shops):
    (shop_a, owner_a), _ = two_shops
    product = _product(shop_a, "Scanner Item")
    product.barcode = "12345"
    with tenant_context(shop_a):
        product.save()
        apply_movement(shop=shop_a, product=product,
                       movement_type=MovementType.OPENING, quantity=10)

    api.force_authenticate(owner_a)

    look = api.get("/api/pos/lookup/?barcode=12345")
    assert look.status_code == 200
    assert look.data["name"] == "Scanner Item"

    checkout = api.post("/api/pos/checkout/", {
        "items": [{"product": product.id, "quantity": "2", "unit_price": "150"}],
        "payments": [{"amount": "300", "method": "cash"}],
    }, format="json")
    assert checkout.status_code == 201
    assert checkout.data["status"] == "paid"
    assert checkout.data["total"] == "300.00"

    product.refresh_from_db()
    assert product.current_stock == Decimal("8")


def test_dashboard_endpoint(api, two_shops):
    (shop_a, owner_a), _ = two_shops
    api.force_authenticate(owner_a)
    resp = api.get("/api/analytics/dashboard/")
    assert resp.status_code == 200
    assert "period" in resp.data and "position" in resp.data


def test_billing_plans_and_status(api, two_shops):
    (shop_a, owner_a), _ = two_shops
    api.force_authenticate(owner_a)
    assert api.get("/api/billing/plans/").status_code == 200
    status_resp = api.get("/api/billing/status/")
    assert status_resp.status_code == 200
    assert status_resp.data["on_trial"] is True
