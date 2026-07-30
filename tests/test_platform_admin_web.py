"""Super Admin dashboard: access control + audited impersonation."""
import pytest
from django.test import Client

from accounts.models import User
from audit.models import AuditLog
from catalog.models import Product

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def staff(db):
    return User.objects.create_superuser(email="root@plat.com", password="pass12345")


def test_staff_login_lands_on_platform_dashboard(client, staff):
    client.login(email="root@plat.com", password="pass12345")
    for path in ["/platform/", "/platform/shops/", "/platform/payments/",
                 "/platform/api-keys/", "/platform/plans/"]:
        assert client.get(path).status_code == 200, path


def test_active_users_table_shows_online_dot(client, staff, two_shops):
    """Feature #1: the active-users table lists a user as online once they've
    made a request (last_seen bumped by LastSeenMiddleware)."""
    (shop_a, owner_a), _ = two_shops
    # Owner hits the app -> middleware records last_seen -> counts as online.
    c2 = Client(); c2.login(email=owner_a.email, password="pass12345")
    c2.get("/app/")
    owner_a.refresh_from_db()
    assert owner_a.last_seen is not None and owner_a.is_online

    client.login(email="root@plat.com", password="pass12345")
    resp = client.get("/platform/users/")
    assert resp.status_code == 200
    assert owner_a.email.encode() in resp.content        # online-only default
    assert client.get("/platform/users/?all=1").status_code == 200


def test_shop_owner_cannot_access_platform(client, two_shops):
    (_, owner_a), _ = two_shops
    client.login(email=owner_a.email, password="pass12345")
    assert client.get("/platform/").status_code == 403
    # And cannot trigger platform actions.
    assert client.post(f"/platform/shops/{owner_a.shop_id}/impersonate/").status_code == 403


def test_impersonation_flow_and_audit(client, staff, two_shops):
    (shop_a, owner_a), _ = two_shops
    client.login(email="root@plat.com", password="pass12345")

    # Start impersonation -> becomes the shop owner, shop frontend works.
    resp = client.post(f"/platform/shops/{shop_a.id}/impersonate/")
    assert resp.status_code == 302
    assert client.session.get("impersonator_id") == staff.id
    assert client.get("/app/").status_code == 200  # shop dashboard, as owner

    # Can act inside the shop (create a product) — lands in shop A only.
    client.post("/products/", {"name": "AdminItem", "cost_price": "1",
                               "selling_price": "2", "reorder_level": "0"})
    assert Product.all_objects.filter(shop_id=shop_a.id, name="AdminItem").exists()

    assert AuditLog.objects.filter(action=AuditLog.Action.IMPERSONATE_START, shop=shop_a).exists()

    # Exit -> back to platform admin.
    out = client.post("/platform/impersonate/stop/")
    assert out.status_code == 302
    assert "impersonator_id" not in client.session
    assert client.get("/platform/").status_code == 200  # staff again
    assert AuditLog.objects.filter(action=AuditLog.Action.IMPERSONATE_END).exists()


def test_impersonation_data_lands_in_correct_shop_only(client, staff, two_shops):
    (shop_a, _), (shop_b, _) = two_shops
    client.login(email="root@plat.com", password="pass12345")
    client.post(f"/platform/shops/{shop_a.id}/impersonate/")
    client.post("/products/", {"name": "ScopedItem", "cost_price": "1",
                               "selling_price": "2", "reorder_level": "0"})
    assert Product.all_objects.filter(shop_id=shop_a.id, name="ScopedItem").exists()
    assert not Product.all_objects.filter(shop_id=shop_b.id, name="ScopedItem").exists()


def test_api_key_regenerate_returns_copyable_key(client, staff, two_shops):
    """Regenerate issues a fresh raw key (shown once) and invalidates the old hash."""
    from core.tenant_context import bypass_tenant_scope
    from public_api.models import APIKey
    (shop_a, _), _ = two_shops
    with bypass_tenant_scope():
        key, old_raw = APIKey.generate(shop=shop_a, name="k", resources=["products"])
        old_hash = key.key_hash
    client.login(email="root@plat.com", password="pass12345")
    resp = client.post(f"/platform/api-keys/{key.id}/regenerate/")
    assert resp.status_code == 302
    # New key stashed for the copy box; old hash replaced.
    assert client.session.get("new_api_key")
    with bypass_tenant_scope():
        key.refresh_from_db()
        assert key.key_hash != old_hash
    # The copy box renders the key on the redirected page.
    page = client.get("/platform/api-keys/").content.decode()
    assert 'id="newKey"' in page


def test_shop_delete_requires_name_confirmation(client, staff, two_shops, widget_model):
    """Wrong name → survives; active shop → blocked; suspended 15d + name → deleted."""
    from datetime import timedelta
    from django.utils import timezone
    from core.tenant_context import bypass_tenant_scope
    from tenants.models import Shop
    from accounts.models import User
    (shop_a, owner_a), _ = two_shops
    client.login(email="root@plat.com", password="pass12345")

    # Wrong name: not deleted.
    client.post(f"/platform/shops/{shop_a.id}/delete/", {"confirm_name": "wrong"})
    with bypass_tenant_scope():
        assert Shop.objects.filter(pk=shop_a.id).exists()

    # Active shop (not suspended) → blocked even with correct name.
    client.post(f"/platform/shops/{shop_a.id}/delete/", {"confirm_name": shop_a.name})
    with bypass_tenant_scope():
        assert Shop.objects.filter(pk=shop_a.id).exists()

    # Suspended but only 3 days → still blocked.
    with bypass_tenant_scope():
        Shop.objects.filter(pk=shop_a.id).update(
            is_active=False, suspended_at=timezone.now() - timedelta(days=3))
    client.post(f"/platform/shops/{shop_a.id}/delete/", {"confirm_name": shop_a.name})
    with bypass_tenant_scope():
        assert Shop.objects.filter(pk=shop_a.id).exists()

    # Suspended 16 days + correct name → permanently deleted, users cascade.
    with bypass_tenant_scope():
        Shop.objects.filter(pk=shop_a.id).update(
            suspended_at=timezone.now() - timedelta(days=16))
    r = client.post(f"/platform/shops/{shop_a.id}/delete/", {"confirm_name": shop_a.name})
    assert r.status_code == 302
    with bypass_tenant_scope():
        assert not Shop.objects.filter(pk=shop_a.id).exists()
        assert not User.objects.filter(pk=owner_a.id).exists()
    from audit.models import AuditLog
    assert AuditLog.objects.filter(target_model="Shop", target_id=str(shop_a.id),
                                   action=AuditLog.Action.DELETE).exists()


def test_super_admin_resets_shop_owner_password(client, staff, two_shops):
    """Feature #9: a platform admin resets a shop owner's password."""
    (shop_a, owner_a), _ = two_shops
    client.login(email="root@plat.com", password="pass12345")
    resp = client.post(f"/platform/shops/{shop_a.id}/owner-password/", {"new_password": "brandnew1"})
    assert resp.status_code == 302
    owner_a.refresh_from_db()
    assert owner_a.check_password("brandnew1")
