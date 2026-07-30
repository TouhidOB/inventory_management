"""Phase 3: warranty, service tickets, AI insights, forecasting, WhatsApp, public API."""
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from catalog.models import Product
from core.tenant_context import tenant_context
from crm.models import Customer
from inventory.models import MovementType
from inventory.services import apply_movement
from sales.services import create_sale

pytestmark = pytest.mark.django_db


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def shop_ctx(two_shops):
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        yield shop_a, owner_a


def _product(shop, name="Router", cost="1000", price="1500"):
    return Product.objects.create(
        shop=shop, name=name, cost_price=Decimal(cost),
        selling_price=Decimal(price), reorder_level=Decimal("5"),
    )


# --- 9.1 warranty -----------------------------------------------------------

def test_warranty_from_sale_item_and_lookup(shop_ctx):
    from service.services import create_warranty_for_sale_item, lookup_warranties
    shop, owner = shop_ctx
    p = _product(shop)
    apply_movement(shop=shop, product=p, movement_type=MovementType.OPENING, quantity=10)
    cust = Customer.objects.create(shop=shop, name="Ali", phone="0170000")
    sale = create_sale(shop=shop, customer=cust, created_by=owner,
                       items=[{"product": p, "quantity": 1, "unit_price": "1500"}])
    item = sale.items.first()
    w = create_warranty_for_sale_item(sale_item=item, period_months=12)
    # expiry computed 12 months out
    assert w.expiry_date.year == w.start_date.year + 1
    assert lookup_warranties(shop, phone="0170000").count() == 1


# --- 9.2 service tickets ----------------------------------------------------

def test_ticket_status_change_records_history_and_notifies(shop_ctx):
    from notifications.models import Notification
    from service.models import ServiceTicket
    from service.services import change_ticket_status, create_ticket
    shop, owner = shop_ctx
    cust = Customer.objects.create(shop=shop, name="Bob")
    t = create_ticket(shop=shop, customer=cust, device_description="Laptop",
                      complaint="No boot", created_by=owner)
    change_ticket_status(ticket=t, new_status=ServiceTicket.Status.READY, changed_by=owner)
    assert t.history.count() == 2  # initial + ready
    assert Notification.all_objects.filter(shop_id=shop.id).exists()


def test_ticket_part_deducts_stock(shop_ctx):
    from service.services import add_ticket_part, create_ticket
    shop, owner = shop_ctx
    p = _product(shop, name="Fan")
    apply_movement(shop=shop, product=p, movement_type=MovementType.OPENING, quantity=10)
    t = create_ticket(shop=shop, customer=None, device_description="PC", complaint="hot", created_by=owner)
    add_ticket_part(ticket=t, product=p, quantity=2, from_stock=True, created_by=owner)
    p.refresh_from_db()
    assert p.current_stock == Decimal("8")




# --- 9.5 WhatsApp -----------------------------------------------------------

def test_whatsapp_respects_consent(shop_ctx):
    from notifications.services import notify_customer_whatsapp
    shop, owner = shop_ctx
    no_consent = Customer.objects.create(shop=shop, name="NC", phone="0171", whatsapp_consent=False)
    assert notify_customer_whatsapp(shop=shop, customer=no_consent,
                                    template_key="service_ticket_update", params=["x"]) is False


def test_whatsapp_webhook_verification(api):
    from django.conf import settings
    resp = api.get(f"/api/notifications/whatsapp/webhook/?hub.mode=subscribe"
                   f"&hub.verify_token={settings.WHATSAPP_VERIFY_TOKEN}&hub.challenge=42")
    assert resp.status_code == 200
    assert resp.content == b"42"
    bad = api.get("/api/notifications/whatsapp/webhook/?hub.mode=subscribe"
                  "&hub.verify_token=wrong&hub.challenge=42")
    assert bad.status_code == 403


# --- 9.7 public API ---------------------------------------------------------

@pytest.fixture
def enterprise_shops(db):
    from tenants.models import SubscriptionPlan
    from tenants.services import register_shop
    ent = SubscriptionPlan.objects.create(
        name="Ent", tier="enterprise", features={"api_access": True}
    )
    a, oa = register_shop(name="EntA", owner_email="ea@ex.com", owner_password="pass12345", plan=ent)
    b, ob = register_shop(name="EntB", owner_email="eb@ex.com", owner_password="pass12345", plan=ent)
    return (a, b)


def test_public_api_key_scoping_and_isolation(api, enterprise_shops):
    from public_api.models import APIKey
    shop_a, shop_b = enterprise_shops
    with tenant_context(shop_a):
        Product.objects.create(shop=shop_a, name="A-item", cost_price=1, selling_price=2)
    with tenant_context(shop_b):
        Product.objects.create(shop=shop_b, name="B-item", cost_price=1, selling_price=2)

    key_a, raw_a = APIKey.generate(shop=shop_a, name="k", resources=["products"])

    # No key -> 403 (permission denies, unauthenticated principal)
    assert api.get("/api/v1/products/").status_code in (401, 403)

    # Key A sees only shop A's products
    resp = api.get("/api/v1/products/", HTTP_X_API_KEY=raw_a)
    assert resp.status_code == 200
    names = {p["name"] for p in resp.data["results"]}
    assert names == {"A-item"}


def test_public_api_write_scope_enforced(api, enterprise_shops):
    from public_api.models import APIKey
    shop_a, _ = enterprise_shops
    ro_key, raw = APIKey.generate(shop=shop_a, name="ro", can_write=False, resources=["products"])
    resp = api.post("/api/v1/products/", {"name": "X", "cost_price": "1", "selling_price": "2"},
                    format="json", HTTP_X_API_KEY=raw)
    assert resp.status_code == 403  # read-only key cannot write


def test_public_api_requires_enterprise_plan(api, two_shops):
    """A key on a non-Enterprise shop is rejected at authentication."""
    from public_api.models import APIKey
    (shop_a, _), _ = two_shops  # conftest plan lacks api_access
    _, raw = APIKey.generate(shop=shop_a, name="k", resources=["products"])
    resp = api.get("/api/v1/products/", HTTP_X_API_KEY=raw)
    assert resp.status_code in (401, 403)


# --- 9.6 API docs -----------------------------------------------------------

def test_openapi_schema_available(api, two_shops):
    (_, owner_a), _ = two_shops
    api.force_authenticate(owner_a)
    assert api.get("/api/schema/").status_code == 200
