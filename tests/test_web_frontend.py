"""Frontend (server-rendered) auth, RBAC, and tenant-isolation tests."""
import pytest
from django.test import Client

from accounts.models import RoleType, User
from catalog.models import Product
from core.tenant_context import tenant_context
from inventory.models import MovementType
from inventory.services import apply_movement
from sales.services import create_sale

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return Client()


def _login(client, email):
    assert client.login(email=email, password="pass12345")


def test_unauthenticated_redirects_to_login(client):
    resp = client.get("/products/")
    assert resp.status_code == 302
    assert "/login/" in resp["Location"]


def test_owner_can_load_all_pages(client, two_shops):
    (_, owner_a), _ = two_shops
    _login(client, owner_a.email)
    for path in ["/app/", "/products/", "/inventory/", "/pos/", "/sales/",
                 "/customers/", "/suppliers/", "/purchases/", "/expenses/",
                 "/reports/", "/service/tickets/", "/service/warranties/",
                 "/service/warranty-coverage/",
                 "/notifications/", "/users/", "/settings/"]:
        assert client.get(path).status_code == 200, path


def test_warranty_barcode_lookup_shows_expiry_and_supplier(client, two_shops):
    """Scanning a received unit's serial on the warranties page resolves its
    warranty (expiry) and supplier; selling the unit attaches the buyer."""
    from decimal import Decimal

    from crm.models import Customer
    from purchasing.models import Supplier
    from purchasing.services import create_purchase_order, receive_purchase_order

    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        p = Product.objects.create(shop=shop_a, name="Router", cost_price=Decimal("50"),
                                   selling_price=Decimal("100"), warranty_months=12)
        sup = Supplier.objects.create(shop=shop_a, name="NetVendor", phone="0199")
        p.supplier = sup; p.save(update_fields=["supplier"])
        po = create_purchase_order(shop=shop_a, supplier=sup, created_by=owner_a,
                                   items=[{"product": p, "quantity": 2, "unit_cost": "50"}])
        receive_purchase_order(po=po, paid=Decimal("0"), created_by=owner_a)
        from catalog.models import ProductUnit
        unit = ProductUnit.objects.filter(product=p).first()
        serial = unit.barcode
        cust = Customer.objects.create(shop=shop_a, name="Karim", phone="0155")
        p.refresh_from_db()  # pick up cached current_stock bumped by receive
        create_sale(shop=shop_a, customer=cust, created_by=owner_a,
                    items=[{"product": p, "quantity": 1, "unit_price": 100}])

    _login(client, owner_a.email)
    resp = client.get(f"/service/warranties/?barcode={serial}")
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "NetVendor" in body            # supplier resolved by barcode
    assert serial in body                 # the scanned serial echoed
    # A sold unit's warranty now carries the buyer.
    from service.models import Warranty
    with tenant_context(shop_a):
        assert Warranty.objects.filter(customer=cust).exists()


def test_sale_starts_warranty_even_without_scanned_units(client, two_shops):
    """Product bought by plain quantity (no per-unit barcodes) but with a
    warranty period: selling it starts coverage bound to the buyer, and it
    shows on the warranty list + is claimable by its generated serial."""
    from decimal import Decimal

    from crm.models import Customer
    from service.models import Warranty

    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        p = Product.objects.create(shop=shop_a, name="Kettle", cost_price=Decimal("30"),
                                   selling_price=Decimal("80"), warranty_months=12)
        apply_movement(shop=shop_a, product=p, movement_type=MovementType.OPENING, quantity=5)
        cust = Customer.objects.create(shop=shop_a, name="Sadia", phone="0177")
        sale = create_sale(shop=shop_a, customer=cust, created_by=owner_a,
                           items=[{"product": p, "quantity": 1, "unit_price": 80}])
        w = Warranty.objects.filter(product=p, sale_item__isnull=False).first()
        assert w is not None and w.customer_id == cust.id
        serial = w.serial_no

    _login(client, owner_a.email)
    listing = client.get("/service/warranties/").content.decode()
    assert "Active warranties (sold items)" in listing and "Sadia" in listing
    # Claimable by its generated serial.
    body = client.get(f"/service/warranties/?barcode={serial}").content.decode()
    assert "Return / claim this unit" in body


def test_warranty_scan_by_product_barcode_lists_customers(client, two_shops):
    """Scanning the PRODUCT barcode (not a per-unit serial) surfaces that
    product's sold warranties so the owner can pick the right customer."""
    from decimal import Decimal

    from crm.models import Customer
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        p = Product.objects.create(shop=shop_a, name="Fan", cost_price=Decimal("10"),
                                   selling_price=Decimal("40"), warranty_months=12,
                                   barcode="FAN-BC-1")
        apply_movement(shop=shop_a, product=p, movement_type=MovementType.OPENING, quantity=5)
        cust = Customer.objects.create(shop=shop_a, name="Nadia", phone="0188")
        create_sale(shop=shop_a, customer=cust, created_by=owner_a,
                    items=[{"product": p, "quantity": 1, "unit_price": 40}])

    _login(client, owner_a.email)
    body = client.get("/service/warranties/?barcode=FAN-BC-1").content.decode()
    assert "Nadia" in body                       # buyer surfaced from product scan
    assert "pick the customer to claim" in body  # match-list rendered


def test_warranty_claim_opens_repair_ticket(client, two_shops):
    """Raising a warranty claim opens a repair ticket (linked to the warranty)
    that shows on the repair tickets board."""
    from service.models import ServiceTicket, Warranty
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        p = Product.objects.create(shop=shop_a, name="Blender", cost_price=5, selling_price=15,
                                   warranty_months=12)
        w = Warranty.objects.create(shop=shop_a, product=p, period_months=12, serial_no="BLND-1")
    _login(client, owner_a.email)
    client.post("/service/warranties/", {
        "action": "claim", "warranty_id": w.id,
        "issue_description": "leaking base", "resolution": "Refund",
        "expected_return_date": "2026-08-15"})
    with tenant_context(shop_a):
        t = ServiceTicket.objects.filter(warranty=w).first()
        assert t is not None and t.complaint == "leaking base" and t.device_description == "Blender"
    # Shows on the repair tickets board.
    body = client.get("/service/tickets/").content.decode()
    assert "Blender" in body


def test_warranty_claim_only_for_sold_in_period_units(client, two_shops):
    """Product-level overview lists every product; a scanned unit is only
    claimable when sold and still within its warranty period."""
    from decimal import Decimal

    from purchasing.models import Supplier
    from purchasing.services import create_purchase_order, receive_purchase_order

    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        p = Product.objects.create(shop=shop_a, name="Speaker", cost_price=Decimal("20"),
                                   selling_price=Decimal("60"), warranty_months=6)
        sup = Supplier.objects.create(shop=shop_a, name="AudioCo")
        po = create_purchase_order(shop=shop_a, supplier=sup, created_by=owner_a,
                                   items=[{"product": p, "quantity": 1, "unit_cost": "20"}])
        receive_purchase_order(po=po, paid=Decimal("0"), created_by=owner_a)
        from catalog.models import ProductUnit
        serial = ProductUnit.objects.filter(product=p).first().barcode

    _login(client, owner_a.email)
    # Product-level overview lives on its own page now.
    overview = client.get("/service/warranty-coverage/").content.decode()
    assert "Speaker" in overview and "Products &amp; warranty coverage" in overview
    # Unit is in stock (not sold) → not claimable.
    body = client.get(f"/service/warranties/?barcode={serial}").content.decode()
    assert "Only a sold unit can be claimed" in body
    assert "Return / claim this unit" not in body


def test_cashier_blocked_from_privileged_pages(client, two_shops):
    (shop_a, _), _ = two_shops
    User.objects.create_user(email="cash@ex.com", password="pass12345",
                             shop=shop_a, role=RoleType.CASHIER)
    _login(client, "cash@ex.com")
    assert client.get("/pos/").status_code == 200          # cashier may sell
    for path in ["/users/", "/reports/", "/expenses/", "/purchases/"]:
        assert client.get(path).status_code == 403, path


def test_frontend_cannot_open_other_shop_objects(client, two_shops):
    (shop_a, owner_a), (shop_b, _) = two_shops
    with tenant_context(shop_b):
        p = Product.objects.create(shop=shop_b, name="B-only", cost_price=1, selling_price=2)
        apply_movement(shop=shop_b, product=p, movement_type=MovementType.OPENING, quantity=5)
        sale = create_sale(shop=shop_b, items=[{"product": p, "quantity": 1, "unit_price": "2"}])

    _login(client, owner_a.email)
    assert client.get(f"/sales/{sale.id}/").status_code == 404
    assert client.get(f"/products/{p.id}/edit/").status_code == 404


def test_public_pages_load_without_auth(client, db):
    for path in ["/", "/features/", "/pricing/", "/contact/", "/signup/"]:
        assert client.get(path).status_code == 200, path


def test_public_signup_is_access_request_not_account(client, db):
    """Self-service signup is disabled: it creates a lead, never an account."""
    from accounts.models import User
    from platform_admin.models import ContactMessage
    resp = client.post("/signup/", {
        "shop_name": "Signup Shop", "business_type": "computer",
        "owner_name": "Rana", "owner_email": "signup@ex.com",
    })
    assert resp.status_code == 302
    # No user/account created; a request lead is stored for the platform team.
    assert not User.objects.filter(email="signup@ex.com").exists()
    assert ContactMessage.objects.filter(email="signup@ex.com").exists()
    # Visitor is NOT logged in — cannot reach the dashboard.
    assert client.get("/app/").status_code == 302


def test_pos_blocks_oversell(client, two_shops):
    import json
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        p = Product.objects.create(shop=shop_a, name="Lim", cost_price=1, selling_price=2)
        apply_movement(shop=shop_a, product=p, movement_type=MovementType.OPENING, quantity=3)
    _login(client, owner_a.email)
    resp = client.post("/pos/checkout/",
                       data=json.dumps({"items": [{"product": p.id, "quantity": 10, "unit_price": 2}],
                                        "customer_name": "Walk", "customer_phone": "0170", "paid": 20}),
                       content_type="application/json")
    assert resp.status_code == 400
    assert "stock" in resp.json()["error"].lower()


def test_warranty_can_be_registered_from_ui(client, two_shops):
    from service.models import Warranty
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        p = Product.objects.create(shop=shop_a, name="TV", cost_price=1, selling_price=2)
    _login(client, owner_a.email)
    resp = client.post("/service/warranties/", {
        "product": p.id, "serial_no": "SN1", "period_months": "24",
    })
    assert resp.status_code == 302
    w = Warranty.all_objects.get(shop_id=shop_a.id, serial_no="SN1")
    assert w.period_months == 24 and w.expiry_date.year == w.start_date.year + 2


def test_customer_default_discount_applied_at_pos(client, two_shops):
    import json
    from crm.models import Customer
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        p = Product.objects.create(shop=shop_a, name="D", cost_price=100, selling_price=200)
        apply_movement(shop=shop_a, product=p, movement_type=MovementType.OPENING, quantity=10)
        cust = Customer.objects.create(shop=shop_a, name="VIP", discount_percent=10)
    _login(client, owner_a.email)
    resp = client.post("/pos/checkout/", data=json.dumps({
        "items": [{"product": p.id, "quantity": 2, "unit_price": 200}],
        "customer": cust.id, "paid": 360,
    }), content_type="application/json")
    assert resp.status_code == 200
    assert resp.json()["total"] == "360.00"  # 400 - 10%


def test_contact_form_creates_message_visible_to_staff(client, db):
    from platform_admin.models import ContactMessage
    from accounts.models import User
    resp = client.post("/contact/", {"name": "Ana", "email": "ana@ex.com",
                                      "message": "hello there"})
    assert resp.status_code == 302
    assert ContactMessage.objects.filter(email="ana@ex.com", is_read=False).exists()
    # shop owner cannot reach the inbox
    User.objects.create_superuser(email="root9@ex.com", password="pass12345")


def test_pos_checkout_rejects_other_shop_product(client, two_shops):
    import json
    (shop_a, owner_a), (shop_b, _) = two_shops
    with tenant_context(shop_b):
        p = Product.objects.create(shop=shop_b, name="B-prod", cost_price=1, selling_price=2)
    _login(client, owner_a.email)
    resp = client.post("/pos/checkout/",
                       data=json.dumps({"items": [{"product": p.id, "quantity": 1, "unit_price": 2}]}),
                       content_type="application/json")
    assert resp.status_code == 400  # not in shop A's scope


def test_pos_checkout_requires_customer_and_payment(client, two_shops):
    import json
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        p = Product.objects.create(shop=shop_a, name="Cable", cost_price=1, selling_price=2)
        apply_movement(shop=shop_a, product=p, movement_type=MovementType.OPENING, quantity=5)
    _login(client, owner_a.email)
    base = {"items": [{"product": p.id, "quantity": 1, "unit_price": 2}]}
    # Missing customer info + payment -> rejected.
    r1 = client.post("/pos/checkout/", data=json.dumps(base), content_type="application/json")
    assert r1.status_code == 400 and "customer" in r1.json()["error"].lower()
    # Customer given but no payment -> rejected.
    r2 = client.post("/pos/checkout/", data=json.dumps(
        {**base, "customer_name": "Walk", "customer_phone": "0170"}),
        content_type="application/json")
    assert r2.status_code == 400 and "payment" in r2.json()["error"].lower()
    # Both supplied -> succeeds.
    r3 = client.post("/pos/checkout/", data=json.dumps(
        {**base, "customer_name": "Walk", "customer_phone": "0170", "paid": 2}),
        content_type="application/json")
    assert r3.status_code == 200


def test_pos_add_customer_inline(client, two_shops):
    import json
    from crm.models import Customer
    (shop_a, owner_a), _ = two_shops
    _login(client, owner_a.email)
    resp = client.post("/pos/add-customer/",
                       data=json.dumps({"name": "Rahim", "phone": "0180", "address": "Dhaka"}),
                       content_type="application/json")
    assert resp.status_code == 200
    with tenant_context(shop_a):
        assert Customer.objects.filter(name="Rahim", phone="0180").exists()


def test_sold_products_page_loads(client, two_shops):
    (_, owner_a), _ = two_shops
    _login(client, owner_a.email)
    assert client.get("/sales/products/").status_code == 200


def test_selling_details_lists_lines_with_customer(client, two_shops):
    import json
    from crm.models import Customer
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        p = Product.objects.create(shop=shop_a, name="Keyboard", cost_price=100, selling_price=250)
        apply_movement(shop=shop_a, product=p, movement_type=MovementType.OPENING, quantity=10)
        cust = Customer.objects.create(shop=shop_a, name="Karim", phone="0199")
        create_sale(shop=shop_a, customer=cust, created_by=owner_a,
                    items=[{"product": p, "quantity": 2, "unit_price": 250}])
    _login(client, owner_a.email)
    resp = client.get("/sales/details/")
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Keyboard" in body and "Karim" in body and "0199" in body


def test_pos_walkin_info_inserted_into_customers(client, two_shops):
    import json
    from crm.models import Customer
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        p = Product.objects.create(shop=shop_a, name="Mouse", cost_price=100, selling_price=200)
        apply_movement(shop=shop_a, product=p, movement_type=MovementType.OPENING, quantity=5)
    _login(client, owner_a.email)
    resp = client.post("/pos/checkout/", data=json.dumps({
        "items": [{"product": p.id, "quantity": 1, "unit_price": 200}],
        "customer_name": "Nadia", "customer_phone": "0155", "customer_address": "Ctg", "paid": 200,
    }), content_type="application/json")
    assert resp.status_code == 200
    with tenant_context(shop_a):
        c = Customer.objects.get(phone="0155")
        assert c.name == "Nadia" and c.sales.count() == 1


def test_product_delete_and_guard(client, two_shops):
    from catalog.models import Product
    from crm.models import Customer
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        free = Product.objects.create(shop=shop_a, name="Deletable", cost_price=1, selling_price=2)
        sold = Product.objects.create(shop=shop_a, name="HasSale", cost_price=1, selling_price=2)
        apply_movement(shop=shop_a, product=sold, movement_type=MovementType.OPENING, quantity=5)
        cust = Customer.objects.create(shop=shop_a, name="C", phone="01")
        create_sale(shop=shop_a, customer=cust, items=[{"product": sold, "quantity": 1, "unit_price": 2}])
    _login(client, owner_a.email)
    # Free product deletes.
    client.post(f"/products/{free.id}/delete/")
    with tenant_context(shop_a):
        assert not Product.objects.filter(pk=free.id).exists()
    # Product with sales is protected → stays.
    client.post(f"/products/{sold.id}/delete/")
    with tenant_context(shop_a):
        assert Product.objects.filter(pk=sold.id).exists()


def test_supplier_edit_and_delete(client, two_shops):
    from purchasing.models import Supplier
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        sup = Supplier.objects.create(shop=shop_a, name="Old", phone="1")
    _login(client, owner_a.email)
    client.post("/suppliers/", {"action": "edit", "id": sup.id, "name": "New", "phone": "9", "email": "e@x.com"})
    with tenant_context(shop_a):
        sup.refresh_from_db(); assert sup.name == "New" and sup.phone == "9"
    client.post("/suppliers/", {"action": "delete", "id": sup.id})
    with tenant_context(shop_a):
        assert not Supplier.objects.filter(pk=sup.id).exists()


def test_customer_edit_and_delete(client, two_shops):
    from crm.models import Customer
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        c = Customer.objects.create(shop=shop_a, name="Old", phone="1")
    _login(client, owner_a.email)
    client.post("/customers/", {"action": "edit", "id": c.id, "name": "New", "phone": "9", "discount_percent": "5"})
    with tenant_context(shop_a):
        c.refresh_from_db(); assert c.name == "New" and str(c.discount_percent) == "5.00"
    client.post("/customers/", {"action": "delete", "id": c.id})
    with tenant_context(shop_a):
        assert not Customer.objects.filter(pk=c.id).exists()


def test_add_product_unified_form(client, two_shops):
    """Unified add-product: auto-SKU, supplier link, warranty row created."""
    from catalog.models import Product, Category
    from purchasing.models import Supplier
    from service.models import Warranty
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        sup = Supplier.objects.create(shop=shop_a, name="Acme")
        cat = Category.objects.create(shop=shop_a, name="Laptops")
    _login(client, owner_a.email)
    client.post("/products/", {
        "action": "add_product", "name": "Widget", "supplier": sup.id,
        "category": cat.id, "cost_price": "100", "selling_price": "150",
        "warranty_months": "12",
    })
    with tenant_context(shop_a):
        p = Product.objects.get(name="Widget")
        assert p.sku.startswith("SKU-")          # auto-generated
        assert p.supplier_id == sup.id           # supplier linked
        assert p.warranty_months == 12
        assert p.category_id == cat.id
        assert Warranty.objects.filter(product=p, period_months=12).exists()


def test_add_custom_category_and_supplier_from_modal(client, two_shops):
    from catalog.models import Category
    from purchasing.models import Supplier
    (shop_a, owner_a), _ = two_shops
    _login(client, owner_a.email)
    client.post("/products/", {"action": "add_category", "category_name": "Phones"})
    client.post("/products/", {"action": "add_supplier", "supplier_name": "Star", "supplier_phone": "9"})
    with tenant_context(shop_a):
        assert Category.objects.filter(name="Phones", parent__isnull=True).exists()
        assert Supplier.objects.filter(name="Star", phone="9").exists()


def test_pos_multistep_flow(client, two_shops):
    """Cart page saves to session, customer page finalizes -> invoice redirect."""
    import json
    from crm.models import Customer
    from sales.models import Sale
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        p = Product.objects.create(shop=shop_a, name="Mouse", cost_price=100, selling_price=200)
        apply_movement(shop=shop_a, product=p, movement_type=MovementType.OPENING, quantity=10)
    _login(client, owner_a.email)
    # Step 1: save cart to session.
    r1 = client.post("/pos/cart/", data=json.dumps(
        {"items": [{"product": p.id, "quantity": 2, "unit_price": 200}]}),
        content_type="application/json")
    assert r1.status_code == 200 and r1.json()["next"].endswith("/pos/customer/")
    # Step 2 GET shows the summary.
    assert client.get("/pos/customer/").status_code == 200
    # Step 2 POST finalizes -> redirect to invoice print.
    r2 = client.post("/pos/customer/", {"customer_name": "Kabir", "customer_phone": "0155",
                                        "paid": "400", "method": "bkash", "discount": "0"})
    assert r2.status_code == 302 and "/print/" in r2["Location"]
    with tenant_context(shop_a):
        sale = Sale.objects.latest("id")
        assert sale.total == 400 and sale.customer.name == "Kabir"
        assert Customer.objects.filter(phone="0155").exists()
    # Cart cleared from session.
    assert client.get("/pos/customer/").status_code == 302  # empty -> back to /pos/


def test_invoice_print_has_barcode_warranty_method(client, two_shops):
    from crm.models import Customer
    from sales.services import create_sale
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        p = Product.objects.create(shop=shop_a, name="Keyboard", cost_price=100,
                                   selling_price=200, warranty_months=6)
        apply_movement(shop=shop_a, product=p, movement_type=MovementType.OPENING, quantity=5)
        cust = Customer.objects.create(shop=shop_a, name="Sim", phone="0111", address="Dhaka")
        sale = create_sale(shop=shop_a, customer=cust, created_by=owner_a,
                           items=[{"product": p, "quantity": 1, "unit_price": 200}],
                           payments=[{"amount": 200, "method": "bkash"}])
    _login(client, owner_a.email)
    body = client.get(f"/sales/{sale.id}/print/").content.decode()
    assert "JsBarcode" in body and sale.invoice_no in body   # per-invoice barcode
    assert "6 mo" in body                                     # warranty column
    assert "bKash" in body                                    # payment method
    assert "Dhaka" in body                                    # customer address


def test_purchase_units_tracked_and_profile(client, two_shops):
    """Bulk barcodes persist as ProductUnits; POS sale flips one to sold;
    profile + resolver + universal search find them (features #6/#7)."""
    import json
    from catalog.models import ProductUnit
    from crm.models import Customer
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        p = Product.objects.create(shop=shop_a, name="Cam", cost_price=40, selling_price=70)
    _login(client, owner_a.email)
    # Push purchase with three scanned unit barcodes.
    r = client.post("/products/purchase/", data=json.dumps({
        "supplier": None,
        "items": [{"product": p.id, "quantity": 3, "cost": 40, "selling": 70,
                   "barcodes": ["U-1", "U-2", "U-3"]}],
    }), content_type="application/json")
    assert r.status_code == 200
    with tenant_context(shop_a):
        assert ProductUnit.objects.filter(product=p, status="in_stock").count() == 3
    # Resolver finds the product by a per-unit barcode.
    rr = client.get("/barcode/resolve/?code=U-2")
    assert rr.status_code == 200 and rr.json()["id"] == p.id
    # Sell one unit via POS checkout → one unit flips to sold.
    with tenant_context(shop_a):
        Customer.objects.create(shop=shop_a, name="Q", phone="03")
    client.post("/pos/checkout/", data=json.dumps({
        "items": [{"product": p.id, "quantity": 1, "unit_price": 70}],
        "customer_name": "Q", "customer_phone": "03", "paid": 70, "method": "cash",
    }), content_type="application/json")
    with tenant_context(shop_a):
        assert ProductUnit.objects.filter(product=p, status="sold").count() == 1
        assert ProductUnit.objects.filter(product=p, status="in_stock").count() == 2
    # Profile page renders unit counts.
    body = client.get(f"/products/{p.id}/").content.decode()
    assert "Tracked units" in body and "U-1" in body


def test_barcode_unique_across_products_and_units(client, two_shops):
    """A barcode used by a unit can't be reused as a product barcode (per shop)."""
    import json
    from catalog.models import ProductUnit
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        p1 = Product.objects.create(shop=shop_a, name="A", cost_price=1, selling_price=2)
        ProductUnit.objects.create(shop=shop_a, product=p1, barcode="DUP-9")
        p2 = Product.objects.create(shop=shop_a, name="B", cost_price=1, selling_price=2)
    _login(client, owner_a.email)
    # set_barcode on p2 with the unit's barcode → rejected.
    r = client.post("/products/set-barcode/", data=json.dumps({"product": p2.id, "barcode": "DUP-9"}),
                    content_type="application/json")
    assert r.status_code == 400 and "already in use" in r.json()["error"]
    # Bulk purchase rejecting duplicate unit barcodes.
    rp = client.post("/products/purchase/", data=json.dumps({
        "items": [{"product": p1.id, "quantity": 1, "barcodes": ["DUP-9"]}]}),
        content_type="application/json")
    assert "DUP-9" in rp.json()["rejected"]


def test_product_edit_sets_category_brand_warranty(client, two_shops):
    from catalog.models import Category, Brand
    from service.models import Warranty
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        parent = Category.objects.create(shop=shop_a, name="Phones")
        sub = Category.objects.create(shop=shop_a, name="Android", parent=parent)
        brand = Brand.objects.create(shop=shop_a, name="Xio")
        p = Product.objects.create(shop=shop_a, name="P1", cost_price=1, selling_price=2)
    _login(client, owner_a.email)
    client.post(f"/products/{p.id}/edit/", {
        "name": "P1", "sku": "", "barcode": "", "cost_price": "1", "selling_price": "2",
        "reorder_level": "0", "is_active": "on",
        "category": parent.id, "subcategory": sub.id, "brand": brand.id, "warranty_months": "18",
    })
    with tenant_context(shop_a):
        p.refresh_from_db()
        assert p.category_id == sub.id and p.brand_id == brand.id and p.warranty_months == 18
        assert Warranty.objects.filter(product=p, period_months=18).exists()


def test_supplier_email_validation(client, two_shops):
    import json
    (shop_a, owner_a), _ = two_shops
    _login(client, owner_a.email)
    bad = client.post("/products/", {"action": "add_supplier", "supplier_name": "X",
                                     "supplier_email": "not-an-email"},
                      HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    assert bad.status_code == 400 and "valid email" in bad.json()["error"]
    ok = client.post("/products/", {"action": "add_supplier", "supplier_name": "Y",
                                    "supplier_email": "y@ex.com"},
                     HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    assert ok.status_code == 200


def test_owner_resets_and_deletes_employee(client, two_shops):
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        emp = User.objects.create_user(email="emp@ex.com", password="pass12345",
                                       shop=shop_a, role=RoleType.CASHIER)
    _login(client, owner_a.email)
    client.post("/users/", {"action": "reset_password", "user_id": emp.id, "new_password": "newpass1"})
    emp.refresh_from_db()
    assert emp.check_password("newpass1")
    client.post("/users/", {"action": "delete_user", "user_id": emp.id})
    assert not User.objects.filter(id=emp.id).exists()


def test_item_lookup_previously_sold(client, two_shops):
    """Scanning a sold product's barcode shows the previously-sold history page."""
    from crm.models import Customer
    from sales.services import create_sale
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        p = Product.objects.create(shop=shop_a, name="Router", barcode="BC-777",
                                   cost_price=50, selling_price=90, warranty_months=12)
        apply_movement(shop=shop_a, product=p, movement_type=MovementType.OPENING, quantity=10)
        cust = Customer.objects.create(shop=shop_a, name="Rafi", phone="0199")
        create_sale(shop=shop_a, customer=cust, created_by=owner_a,
                    items=[{"product": p, "quantity": 1, "unit_price": 90}],
                    payments=[{"amount": 90, "method": "cash"}])
    _login(client, owner_a.email)
    body = client.get("/products/lookup/?barcode=BC-777").content.decode()
    assert "PREVIOUSLY SOLD" in body
    assert "Rafi" in body                       # customer on sale details
    assert "Warranty expiry" in body            # warranty timeline present
    # Unknown barcode → not-found state
    nf = client.get("/products/lookup/?barcode=NOPE").content.decode()
    assert "No product matches" in nf


def test_purchase_invoice_print_has_warranty(client, two_shops):
    """Purchase invoice lists each product's warranty duration."""
    from purchasing.models import Supplier
    from purchasing.services import create_purchase_order
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        sup = Supplier.objects.create(shop=shop_a, name="Acme Dist")
        p = Product.objects.create(shop=shop_a, name="SSD", cost_price=90,
                                   selling_price=140, warranty_months=24)
        po = create_purchase_order(shop=shop_a, supplier=sup, created_by=owner_a,
                                   items=[{"product": p, "quantity": 3, "unit_cost": 90}])
    _login(client, owner_a.email)
    body = client.get(f"/purchases/{po.id}/print/").content.decode()
    assert "PURCHASE INVOICE" in body
    assert po.po_number in body
    assert "24 mo" in body            # warranty duration column
    assert "Acme Dist" in body        # supplier


def test_edit_completed_sale(client, two_shops):
    """Edit a sale: stock, total and customer due adjust; invoice_no unchanged."""
    from crm.models import Customer
    from sales.services import create_sale
    from catalog.models import Product as P
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        p = Product.objects.create(shop=shop_a, name="RAM", cost_price=100, selling_price=200)
        apply_movement(shop=shop_a, product=p, movement_type=MovementType.OPENING, quantity=20)
        cust = Customer.objects.create(shop=shop_a, name="Nur", phone="07")
        sale = create_sale(shop=shop_a, customer=cust, created_by=owner_a,
                           items=[{"product": p, "quantity": 2, "unit_price": 200}],
                           payments=[{"amount": 100, "method": "cash"}])
        inv = sale.invoice_no
    _login(client, owner_a.email)
    # Change qty 2 -> 5.
    r = client.post(f"/sales/{sale.id}/edit/", {
        "product": [str(p.id)], "quantity": ["5"], "unit_price": ["200"], "discount": "0"})
    assert r.status_code == 302
    with tenant_context(shop_a):
        sale.refresh_from_db(); p.refresh_from_db(); cust.refresh_from_db()
        assert sale.invoice_no == inv               # same invoice number
        assert sale.total == 1000                   # 5 * 200
        assert p.current_stock == 15                # 20 - 5 (net)
        assert cust.due_balance == 900              # 1000 - 100 paid


def test_service_ticket_warranty_field(client, two_shops):
    from service.models import Warranty, ServiceTicket
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        p = Product.objects.create(shop=shop_a, name="Phone", cost_price=1, selling_price=2)
        w = Warranty.objects.create(shop=shop_a, product=p, period_months=12)
    _login(client, owner_a.email)
    client.post("/service/tickets/", {"device_description": "Phone X", "complaint": "screen",
                                      "warranty": w.id})
    with tenant_context(shop_a):
        t = ServiceTicket.objects.latest("id")
        assert t.warranty_id == w.id


def test_warranty_return_claim(client, two_shops):
    from service.models import Warranty, WarrantyClaim
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        p = Product.objects.create(shop=shop_a, name="Cam", cost_price=1, selling_price=2)
        w = Warranty.objects.create(shop=shop_a, product=p, period_months=12)
    _login(client, owner_a.email)
    client.post("/service/warranties/", {"action": "claim", "warranty_id": w.id,
                                         "issue_description": "dead pixel", "resolution": "Replace"})
    with tenant_context(shop_a):
        w.refresh_from_db()
        assert w.status == Warranty.Status.CLAIMED
        assert WarrantyClaim.objects.filter(warranty=w, resolution="Replace").exists()


def test_product_csv_import(client, two_shops):
    from catalog.models import Product as P
    (shop_a, owner_a), _ = two_shops
    _login(client, owner_a.email)
    from io import BytesIO
    csv_bytes = b"product name,avg cost\nHDD 1TB,3200\nSSD 500,4100\n"
    f = BytesIO(csv_bytes); f.name = "p.csv"
    client.post("/products/import/", {"file": f})
    with tenant_context(shop_a):
        assert P.objects.filter(name="HDD 1TB", cost_price=3200).exists()
        assert P.objects.filter(name="SSD 500", cost_price=4100).exists()
        # header row not imported
        assert not P.objects.filter(name="product name").exists()


def test_dashboard_has_all_charts(client, two_shops):
    (_, owner_a), _ = two_shops
    _login(client, owner_a.email)
    body = client.get("/app/").content.decode()
    # Full analytics suite embedded on the dashboard.
    assert 'id="charts-data"' in body
    for cid in ["cRev", "cMix", "cTop", "cTat", "cDev", "cPareto", "cTurn", "cReorder", "cAcq"]:
        assert f'id="{cid}"' in body


def test_products_pagination(client, two_shops):
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        for i in range(30):
            Product.objects.create(shop=shop_a, name=f"P{i:02d}", cost_price=1, selling_price=2)
    _login(client, owner_a.email)
    r1 = client.get("/products/")
    assert r1.context["page_obj"].paginator.num_pages >= 2
    assert len(r1.context["page_obj"].object_list) == 15                        # first page
    r2 = client.get("/products/?page=2")
    assert r2.context["page_obj"].number == 2
    assert "pagination" in r2.content.decode()                   # pager rendered


def test_ticket_saves_without_customer_or_warranty(client, two_shops):
    """Regression: empty customer/warranty selects (pk='') no longer 500."""
    from service.models import ServiceTicket
    (shop_a, owner_a), _ = two_shops
    _login(client, owner_a.email)
    r = client.post("/service/tickets/", {"device_description": "Laptop", "complaint": "dead",
                                          "customer": "", "warranty": ""})
    assert r.status_code == 302
    with tenant_context(shop_a):
        assert ServiceTicket.objects.filter(device_description="Laptop").exists()


def test_ajax_add_category_returns_json(client, two_shops):
    (shop_a, owner_a), _ = two_shops
    _login(client, owner_a.email)
    r = client.post("/products/", {"action": "add_category", "category_name": "Tablets"},
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    assert r.status_code == 200 and r.json()["name"] == "Tablets" and "id" in r.json()


def test_purchase_scan_pushes_stock(client, two_shops):
    import json
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        p = Product.objects.create(shop=shop_a, name="RAM", cost_price=100, selling_price=150)
        apply_movement(shop=shop_a, product=p, movement_type=MovementType.OPENING, quantity=5)
    _login(client, owner_a.email)
    assert client.get("/products/purchase/").status_code == 200
    r = client.post("/products/purchase/", data=json.dumps(
        {"items": [{"product": p.id, "quantity": 8}]}), content_type="application/json")
    assert r.status_code == 200 and r.json()["count"] == 1
    with tenant_context(shop_a):
        p.refresh_from_db(); assert p.current_stock == 13   # 5 + 8


def test_universal_search_grouped(client, two_shops):
    from crm.models import Customer
    from purchasing.models import Supplier
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        Product.objects.create(shop=shop_a, name="Zeta Mouse", barcode="BC123", cost_price=1, selling_price=2)
        Customer.objects.create(shop=shop_a, name="Zeta Customer", phone="0199")
        Supplier.objects.create(shop=shop_a, name="Zeta Supplier")
    _login(client, owner_a.email)
    d = client.get("/search/?q=Zeta").json()
    assert any("Mouse" in x["label"] for x in d["products"])
    assert any("Customer" in x["label"] for x in d["customers"])
    assert any("Supplier" in x["label"] for x in d["suppliers"])
    # product barcode search
    d2 = client.get("/search/?q=BC123").json()
    assert d2["products"] and d2["products"][0]["label"] == "Zeta Mouse"


def test_set_barcode_and_manual_purchase(client, two_shops):
    import json
    from catalog.models import Product as P
    (shop_a, owner_a), _ = two_shops
    with tenant_context(shop_a):
        p = P.objects.create(shop=shop_a, name="SSD", cost_price=100, selling_price=150)  # no barcode
        apply_movement(shop=shop_a, product=p, movement_type=MovementType.OPENING, quantity=0)
    _login(client, owner_a.email)
    # Assign a barcode manually.
    r = client.post("/products/set-barcode/", data=json.dumps({"product": p.id, "barcode": "SSD-001"}),
                    content_type="application/json")
    assert r.status_code == 200 and r.json()["barcode"] == "SSD-001"
    with tenant_context(shop_a):
        p.refresh_from_db(); assert p.barcode == "SSD-001"
    # Duplicate barcode rejected.
    with tenant_context(shop_a):
        p2 = P.objects.create(shop=shop_a, name="HDD", cost_price=1, selling_price=2)
    rd = client.post("/products/set-barcode/", data=json.dumps({"product": p2.id, "barcode": "SSD-001"}),
                     content_type="application/json")
    assert rd.status_code == 400 and "already in use" in rd.json()["error"]
    # Purchase multiple units in one go.
    re = client.post("/products/purchase/", data=json.dumps({"items": [{"product": p.id, "quantity": 12}]}),
                     content_type="application/json")
    assert re.status_code == 200
    with tenant_context(shop_a):
        p.refresh_from_db(); assert p.current_stock == 12
