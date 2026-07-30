import pytest

from accounts.models import Role, RoleType, User
from tenants.models import Branch, Subscription

pytestmark = pytest.mark.django_db


def test_register_shop_provisions_everything(two_shops):
    (shop_a, owner_a), _ = two_shops
    assert owner_a.role == RoleType.OWNER
    assert owner_a.shop_id == shop_a.id
    assert shop_a.on_trial is True
    assert Branch.objects.filter(shop=shop_a, is_main=True).count() == 1
    # Default system roles seeded (manager/cashier/inventory/accountant/owner).
    assert Role.objects.filter(shop=shop_a, is_system=True).count() == 5
    assert Subscription.objects.filter(
        shop=shop_a, status=Subscription.Status.TRIALING
    ).exists()


def test_owner_has_all_permissions(two_shops):
    (_, owner_a), _ = two_shops
    assert owner_a.has_perm_code("delete_sale") is True
    assert owner_a.has_perm_code("view_profit") is True


def test_cashier_permissions_are_limited(two_shops):
    (shop_a, _), _ = two_shops
    cashier = User.objects.create_user(
        email="cash@ex.com", password="pass12345",
        shop=shop_a, role=RoleType.CASHIER,
    )
    assert cashier.has_perm_code("create_sale") is True
    assert cashier.has_perm_code("view_profit") is False
    assert cashier.has_perm_code("delete_sale") is False


def test_perm_check_is_tenant_scoped(two_shops):
    """A user's role in shop A must not grant perms via shop B's roles."""
    (shop_a, _), (shop_b, _) = two_shops
    acc = User.objects.create_user(
        email="acc@ex.com", password="pass12345",
        shop=shop_a, role=RoleType.ACCOUNTANT,
    )
    assert acc.has_perm_code("manage_accounting") is True
    # Remove shop A accountant's perm; shop B still has it, must not leak.
    Role.objects.get(shop=shop_a, role_type=RoleType.ACCOUNTANT).permissions.clear()
    assert acc.has_perm_code("manage_accounting") is False
