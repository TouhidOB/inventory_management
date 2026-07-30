"""Owner-defined custom roles: create, assign, permission resolution, delete."""
import pytest

from accounts.models import Role, User
from core.tenant_context import tenant_context

pytestmark = pytest.mark.django_db


def _login(client, email):
    assert client.login(email=email, password="pass12345")


def test_owner_creates_and_assigns_custom_role(client, two_shops):
    (shop_a, owner_a), _ = two_shops
    _login(client, owner_a.email)

    # Owner defines a custom role with a specific permission.
    client.post("/users/", {"action": "create_role", "role_name": "Floor Staff",
                            "codes": ["create_sale"]})
    with tenant_context(shop_a):
        role = Role.objects.get(shop_id=shop_a.id, name="Floor Staff")
        assert role.is_system is False and role.role_type == "floor_staff"

    # Assign a new employee to that custom role.
    client.post("/users/", {"action": "create_user", "email": "fs@ex.com",
                            "password": "pass12345", "role": role.role_type})
    with tenant_context(shop_a):
        u = User.objects.get(email="fs@ex.com")
        assert u.role == role.role_type
        assert u.has_perm_code("create_sale") is True      # granted
        assert u.has_perm_code("manage_users") is False     # not granted


def test_custom_role_slug_is_unique_per_shop(client, two_shops):
    (shop_a, owner_a), _ = two_shops
    _login(client, owner_a.email)
    client.post("/users/", {"action": "create_role", "role_name": "Tech"})
    client.post("/users/", {"action": "create_role", "role_name": "Tech"})
    with tenant_context(shop_a):
        slugs = set(Role.objects.filter(shop_id=shop_a.id, name="Tech").values_list("role_type", flat=True))
        assert slugs == {"tech", "tech_2"}


def test_reassign_employee_role(client, two_shops):
    (shop_a, owner_a), _ = two_shops
    _login(client, owner_a.email)
    client.post("/users/", {"action": "create_role", "role_name": "Helper", "codes": ["view_inventory"]})
    with tenant_context(shop_a):
        helper = Role.objects.get(shop_id=shop_a.id, name="Helper")
        emp = User.objects.create_user(email="e@ex.com", password="pass12345",
                                       shop=shop_a, role="cashier")
    client.post("/users/", {"action": "set_role", "user_id": emp.id, "role": helper.role_type})
    with tenant_context(shop_a):
        emp.refresh_from_db()
        assert emp.role == helper.role_type


def test_cannot_delete_role_with_members(client, two_shops):
    (shop_a, owner_a), _ = two_shops
    _login(client, owner_a.email)
    client.post("/users/", {"action": "create_role", "role_name": "Busy"})
    with tenant_context(shop_a):
        role = Role.objects.get(shop_id=shop_a.id, name="Busy")
        User.objects.create_user(email="busy@ex.com", password="pass12345",
                                 shop=shop_a, role=role.role_type)
    client.post("/users/", {"action": "delete_role", "role_id": role.id})
    with tenant_context(shop_a):
        assert Role.objects.filter(pk=role.id).exists()  # blocked, still there


def test_create_user_with_inline_new_role(client, two_shops):
    (shop_a, owner_a), _ = two_shops
    _login(client, owner_a.email)
    # Dropdown "__new__" path: role created on the fly and assigned to the user.
    client.post("/users/", {"action": "create_user", "email": "inline@ex.com",
                            "password": "pass12345", "role": "__new__",
                            "role_new": "Night Shift"})
    with tenant_context(shop_a):
        role = Role.objects.get(shop_id=shop_a.id, name="Night Shift")
        assert role.is_system is False and role.role_type == "night_shift"
        u = User.objects.get(email="inline@ex.com")
        assert u.role == role.role_type
