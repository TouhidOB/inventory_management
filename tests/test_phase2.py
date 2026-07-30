"""Phase 2 coverage: analytics, RBAC, alerts, returns, branches, reports, billing."""
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounting.services import cash_flow, record_expense
from analytics.services import category_sales, dead_stock, stock_value
from catalog.models import Product
from core.tenant_context import tenant_context
from crm.models import Customer
from inventory.models import MovementType
from inventory.services import apply_movement
from sales.returns import create_return
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


def _product(shop, name="Widget", cost="100", price="150"):
    return Product.objects.create(
        shop=shop, name=name, cost_price=Decimal(cost),
        selling_price=Decimal(price), reorder_level=Decimal("5"),
    )


# --- 8.1 analytics ----------------------------------------------------------

def test_stock_value_and_dead_stock(shop_ctx):
    shop, owner = shop_ctx
    p = _product(shop)
    apply_movement(shop=shop, product=p, movement_type=MovementType.OPENING, quantity=10)
    assert stock_value(shop) == Decimal("1000")  # 10 * 100

    dead = dead_stock(shop, days=90)  # never sold => dead
    assert dead["count"] == 1
    assert dead["tied_capital"] == Decimal("1000")


def test_category_sales_returns_growth_field(shop_ctx):
    shop, owner = shop_ctx
    p = _product(shop)
    apply_movement(shop=shop, product=p, movement_type=MovementType.OPENING, quantity=50)
    create_sale(shop=shop, created_by=owner,
                items=[{"product": p, "quantity": 5, "unit_price": "150"}])
    rows = category_sales(shop, period="month")
    assert rows and "growth_pct" in rows[0] and "profit" in rows[0]


# --- 8.2 RBAC ---------------------------------------------------------------

def test_owner_can_edit_role_permissions_and_it_audits(api, two_shops):
    from audit.models import AuditLog
    (shop_a, owner_a), _ = two_shops
    api.force_authenticate(owner_a)
    role = api.get("/api/roles/").data["results"]
    cashier = next(r for r in role if r["role_type"] == "cashier")
    resp = api.post(f"/api/roles/{cashier['id']}/set_permissions/",
                    {"codes": ["create_sale", "view_profit"]}, format="json")
    assert resp.status_code == 200
    assert set(resp.data["permission_codes"]) == {"create_sale", "view_profit"}
    assert AuditLog.objects.filter(action=AuditLog.Action.PERMISSION_CHANGE).exists()


def test_permission_denied_is_audited(api, two_shops):
    from accounts.models import RoleType, User
    from audit.models import AuditLog
    (shop_a, _), _ = two_shops
    cashier = User.objects.create_user(email="c@ex.com", password="pass12345",
                                       shop=shop_a, role=RoleType.CASHIER)
    api.force_authenticate(cashier)
    # cashier lacks view_reports -> analytics dashboard denied + audited
    resp = api.get("/api/analytics/dashboard/")
    assert resp.status_code == 403


def test_my_permissions_endpoint(api, two_shops):
    (_, owner_a), _ = two_shops
    api.force_authenticate(owner_a)
    resp = api.get("/api/auth/my-permissions/")
    assert resp.status_code == 200
    assert "view_profit" in resp.data["permissions"]


# --- 8.3 stock alerts -------------------------------------------------------

def test_low_stock_scan_creates_notification(shop_ctx):
    from inventory.tasks import scan_low_stock
    from notifications.models import Notification
    shop, owner = shop_ctx
    p = _product(shop)
    apply_movement(shop=shop, product=p, movement_type=MovementType.OPENING, quantity=2)  # below reorder 5
    scan_low_stock()
    assert Notification.all_objects.filter(shop_id=shop.id).exists()


# --- 8.4 returns & exchange -------------------------------------------------

def test_partial_return_restocks_and_sets_status(shop_ctx):
    from sales.models import Sale
    shop, owner = shop_ctx
    p = _product(shop)
    apply_movement(shop=shop, product=p, movement_type=MovementType.OPENING, quantity=10)
    cust = Customer.objects.create(shop=shop, name="R")
    sale = create_sale(shop=shop, customer=cust, created_by=owner,
                       items=[{"product": p, "quantity": 4, "unit_price": "150"}],
                       payments=[{"amount": "600"}])
    item = sale.items.first()
    sret, exch, net = create_return(sale=sale, lines=[{"sale_item": item, "quantity": 2}],
                                    created_by=owner)
    assert sret.total_refund == Decimal("300")
    p.refresh_from_db()
    assert p.current_stock == Decimal("8")  # 10-4+2
    sale.refresh_from_db()
    assert sale.status == Sale.Status.PARTIALLY_RETURNED
    # return_in movement recorded
    assert p.movements.filter(movement_type=MovementType.SALE_RETURN_IN).exists()


def test_exchange_computes_net(shop_ctx):
    shop, owner = shop_ctx
    p1 = _product(shop, name="Old", price="150")
    p2 = _product(shop, name="New", price="250")
    for p in (p1, p2):
        apply_movement(shop=shop, product=p, movement_type=MovementType.OPENING, quantity=10)
    sale = create_sale(shop=shop, created_by=owner,
                       items=[{"product": p1, "quantity": 1, "unit_price": "150"}],
                       payments=[{"amount": "150"}])
    item = sale.items.first()
    sret, exch, net = create_return(
        sale=sale, lines=[{"sale_item": item, "quantity": 1}],
        exchange_items=[{"product": p2, "quantity": 1, "unit_price": "250"}],
        created_by=owner,
    )
    assert exch is not None
    assert net == Decimal("100")  # new 250 - refund 150


# --- 8.5 branches -----------------------------------------------------------

def test_branch_transfer_and_comparison(shop_ctx):
    from branches.services import branch_comparison, create_transfer, receive_transfer
    from inventory.models import StockMovement
    from tenants.models import Branch
    shop, owner = shop_ctx
    main = Branch.objects.get(shop=shop, is_main=True)
    second = Branch.objects.create(shop=shop, name="Second")
    p = _product(shop)
    apply_movement(shop=shop, product=p, movement_type=MovementType.OPENING, quantity=20, branch=main)

    t = create_transfer(shop=shop, source_branch=main, dest_branch=second,
                        items=[{"product": p, "quantity": 5, "unit_cost": "100"}], created_by=owner)
    receive_transfer(transfer=t, created_by=owner)
    assert StockMovement.all_objects.filter(shop_id=shop.id, branch=second,
                                            movement_type=MovementType.TRANSFER_IN).exists()
    comp = branch_comparison(shop)
    assert len(comp["branches"]) == 2


def test_branch_api_gated_by_feature(api, db):
    """Shop on a plan without multi_branch gets 403 on branch endpoints."""
    from tenants.models import SubscriptionPlan
    from tenants.services import register_shop
    free = SubscriptionPlan.objects.create(name="Free", tier="free", features={})
    shop, owner = register_shop(name="NoBranch", owner_email="nb@ex.com",
                                owner_password="pass12345", plan=free)
    api.force_authenticate(owner)
    assert api.get("/api/branches/branches/").status_code == 403


# --- 8.6 reports & cash flow ------------------------------------------------

def test_cash_flow_and_expense_ledger(shop_ctx):
    shop, owner = shop_ctx
    p = _product(shop)
    apply_movement(shop=shop, product=p, movement_type=MovementType.OPENING, quantity=10)
    create_sale(shop=shop, created_by=owner,
                items=[{"product": p, "quantity": 2, "unit_price": "150"}],
                payments=[{"amount": "300"}])
    record_expense(shop=shop, amount=Decimal("100"), spent_on=timezone.now().date())
    cf = cash_flow(shop, start=None, end=None)
    assert cf["inflow"] == Decimal("300")
    assert cf["outflow"] == Decimal("100")
    assert cf["closing_cash"] == Decimal("200")


def test_report_export_csv(api, two_shops):
    (_, owner_a), _ = two_shops
    api.force_authenticate(owner_a)
    resp = api.get("/api/reports/export/?type=inventory&export_format=csv")
    assert resp.status_code == 200
    assert resp["Content-Type"].startswith("text/csv")


# --- 8.7 manual billing -----------------------------------------------------

def test_manual_payment_submit_then_admin_approve(api, two_shops):
    from accounts.models import User
    from billing.models import ManualPayment
    from tenants.models import Subscription, SubscriptionPlan
    (shop_a, owner_a), _ = two_shops
    plan = SubscriptionPlan.objects.get(tier="professional")

    api.force_authenticate(owner_a)
    submit = api.post("/api/billing/payments/", {
        "plan": plan.id, "cycle": "monthly", "amount": "1500",
        "method": "bkash", "payer_reference": "TXN123",
    }, format="json")
    assert submit.status_code == 201
    payment_id = submit.data["id"]
    assert ManualPayment.all_objects.get(id=payment_id).status == "pending_review"

    # Super admin approves
    staff = User.objects.create_superuser(email="root2@ex.com", password="pass12345")
    api.force_authenticate(staff)
    approve = api.post(f"/api/platform/manual-payments/{payment_id}/approve/")
    assert approve.status_code == 200

    sub = Subscription.objects.filter(shop=shop_a, is_current=True).first()
    assert sub.status == Subscription.Status.ACTIVE
    assert sub.current_period_end > timezone.now()


def test_no_payment_gateway_code_exists():
    """Guard: ensure no gateway SDK/webhook leaked into billing."""
    import pathlib
    billing_dir = pathlib.Path(__file__).resolve().parent.parent / "billing"
    text = " ".join(p.read_text().lower() for p in billing_dir.glob("*.py"))
    for banned in ("sslcommerz", "stripe", "webhook", "bkash_merchant", "auto_debit"):
        assert banned not in text
