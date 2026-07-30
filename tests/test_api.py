import pytest
from rest_framework.test import APIClient

from core.gating import require_feature
from core.exceptions import FeatureNotAvailable

pytestmark = pytest.mark.django_db


@pytest.fixture
def api():
    return APIClient()


def test_register_endpoint_returns_tokens(api, plan):
    resp = api.post("/api/auth/register/", {
        "shop_name": "New Shop", "owner_email": "new@ex.com",
        "owner_password": "pass12345", "business_type": "mobile",
    }, format="json")
    assert resp.status_code == 201
    assert "access" in resp.data and "refresh" in resp.data
    assert resp.data["user"]["role"] == "owner"


def test_me_endpoint_requires_auth(api):
    assert api.get("/api/auth/me/").status_code == 401


def test_me_endpoint_returns_current_user(api, two_shops):
    (_, owner_a), _ = two_shops
    api.force_authenticate(owner_a)
    resp = api.get("/api/auth/me/")
    assert resp.status_code == 200
    assert resp.data["email"] == owner_a.email


def test_feature_gate_blocks_missing_feature(plan, two_shops):
    (shop_a, _), _ = two_shops
    assert require_feature(shop_a, "advanced_analytics") is True
    with pytest.raises(FeatureNotAvailable):
        require_feature(shop_a, "api_access")  # not in Pro plan


def test_platform_dashboard_requires_staff(api, two_shops):
    (_, owner_a), _ = two_shops
    api.force_authenticate(owner_a)
    assert api.get("/api/platform/dashboard/").status_code == 403


def test_platform_impersonation_flow(api, two_shops):
    from accounts.models import User
    from audit.models import AuditLog
    (shop_a, _), _ = two_shops
    staff = User.objects.create_superuser(email="root@ex.com", password="pass12345")
    api.force_authenticate(staff)

    r = api.post(f"/api/platform/shops/{shop_a.id}/impersonate/")
    assert r.status_code == 200
    assert AuditLog.objects.filter(
        action=AuditLog.Action.IMPERSONATE_START, shop=shop_a
    ).exists()

    r = api.post("/api/platform/impersonate/stop/")
    assert r.status_code == 200
    assert AuditLog.objects.filter(action=AuditLog.Action.IMPERSONATE_END).exists()
