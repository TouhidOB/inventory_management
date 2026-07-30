"""
Hard IDOR audit: shop A must never read or mutate shop B's objects by guessing
IDs. Every attempt must 404 (scoped queryset) or 403 (permission), never 200
with another tenant's data.
"""
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from catalog.models import Product
from core.tenant_context import tenant_context
from crm.models import Customer
from purchasing.models import Supplier
from sales.services import create_sale
from inventory.services import apply_movement
from inventory.models import MovementType

pytestmark = pytest.mark.django_db


@pytest.fixture
def api():
    return APIClient()


def _seed(shop):
    with tenant_context(shop):
        p = Product.objects.create(shop=shop, name=f"{shop.name}-prod",
                                   cost_price=Decimal("10"), selling_price=Decimal("20"))
        apply_movement(shop=shop, product=p, movement_type=MovementType.OPENING, quantity=20)
        c = Customer.objects.create(shop=shop, name=f"{shop.name}-cust", phone="019")
        s = Supplier.objects.create(shop=shop, name=f"{shop.name}-supp")
        sale = create_sale(shop=shop, customer=c,
                           items=[{"product": p, "quantity": 1, "unit_price": "20"}])
    return {"product": p.id, "customer": c.id, "supplier": s.id, "sale": sale.id}


def test_shop_a_cannot_read_shop_b_objects(api, two_shops):
    (shop_a, owner_a), (shop_b, owner_b) = two_shops
    b = _seed(shop_b)
    api.force_authenticate(owner_a)  # authenticated as shop A

    # Detail reads of shop B's objects must 404 (scoped out of existence).
    assert api.get(f"/api/catalog/products/{b['product']}/").status_code == 404
    assert api.get(f"/api/crm/customers/{b['customer']}/").status_code == 404
    assert api.get(f"/api/purchasing/suppliers/{b['supplier']}/").status_code == 404
    assert api.get(f"/api/sales/sales/{b['sale']}/").status_code == 404


def test_shop_a_cannot_mutate_shop_b_objects(api, two_shops):
    (shop_a, owner_a), (shop_b, owner_b) = two_shops
    b = _seed(shop_b)
    api.force_authenticate(owner_a)

    # Update / delete / action on shop B's objects must 404.
    assert api.patch(f"/api/catalog/products/{b['product']}/", {"name": "hacked"},
                     format="json").status_code == 404
    assert api.delete(f"/api/crm/customers/{b['customer']}/").status_code == 404
    assert api.post(f"/api/sales/sales/{b['sale']}/add_payment/", {"amount": "5"},
                    format="json").status_code == 404
    # Shop B's product still intact.
    b_prod = Product.all_objects.get(id=b["product"])
    assert b_prod.name == "Beta Mobiles-prod"


def test_shop_a_cannot_sell_shop_b_product(api, two_shops):
    """Creating a sale referencing another shop's product must be rejected."""
    (shop_a, owner_a), (shop_b, _) = two_shops
    b = _seed(shop_b)
    api.force_authenticate(owner_a)
    resp = api.post("/api/pos/checkout/", {
        "items": [{"product": b["product"], "quantity": "1", "unit_price": "20"}],
    }, format="json")
    assert resp.status_code == 400  # product not in shop A's scope


def test_list_endpoints_never_include_other_shop_rows(api, two_shops):
    (shop_a, owner_a), (shop_b, _) = two_shops
    _seed(shop_a)
    _seed(shop_b)
    api.force_authenticate(owner_a)
    for url in ["/api/catalog/products/", "/api/crm/customers/",
                "/api/purchasing/suppliers/", "/api/sales/sales/"]:
        rows = api.get(url).data["results"]
        assert all("Beta" not in str(r) for r in rows), f"leak in {url}"
