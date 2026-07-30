"""
RBAC catalog + default role→permission seeding.

``PERMISSION_CATALOG`` is the source of truth for feature-level permission codes.
``DEFAULT_ROLE_PERMISSIONS`` maps each RoleType to the codes it gets by default
when a shop is created. Owner is intentionally omitted — owners bypass checks in
``User.has_perm_code`` and implicitly hold every permission.
"""
from .models import Permission, Role, RoleType

# (code, human name, category)
PERMISSION_CATALOG = [
    ("manage_users", "Manage users & roles", "admin"),
    ("manage_settings", "Manage shop settings", "admin"),
    ("view_sales", "View sales", "sales"),
    ("view_profit", "View profit figures", "reports"),
    ("view_reports", "View reports", "reports"),
    ("manage_expenses", "Manage expenses", "accounting"),
    ("manage_products", "Create/edit products", "catalog"),
    ("manage_inventory", "Adjust & transfer stock", "inventory"),
    ("view_inventory", "View stock levels", "inventory"),
    ("create_sale", "Create sales at POS", "sales"),
    ("delete_sale", "Delete/void sales", "sales"),
    ("process_return", "Process returns & exchanges", "sales"),
    ("manage_purchasing", "Manage purchase orders & suppliers", "purchasing"),
    ("manage_customers", "Manage customers", "crm"),
    ("manage_accounting", "Manage income/expense entries", "accounting"),
    ("manage_service", "Manage warranties & service tickets", "service"),
    ("view_service", "View warranties & service tickets", "service"),
]

DEFAULT_ROLE_PERMISSIONS = {
    RoleType.MANAGER: [
        "manage_users", "manage_settings", "view_sales", "view_profit", "view_reports",
        "manage_products", "manage_inventory", "view_inventory", "create_sale",
        "delete_sale", "process_return", "manage_purchasing", "manage_customers",
        "manage_accounting", "manage_expenses", "manage_service", "view_service",
    ],
    RoleType.CASHIER: [
        "view_sales", "create_sale", "process_return", "view_inventory", "manage_customers",
        "view_service",
    ],
    RoleType.INVENTORY_MANAGER: [
        "manage_products", "manage_inventory", "view_inventory",
        "manage_purchasing", "view_reports",
    ],
    RoleType.ACCOUNTANT: [
        "view_sales", "view_profit", "view_reports", "manage_accounting",
        "manage_expenses", "view_inventory",
    ],
}


def sync_permission_catalog():
    """Idempotently upsert the global permission catalog. Safe to re-run."""
    for code, name, category in PERMISSION_CATALOG:
        Permission.objects.update_or_create(
            code=code, defaults={"name": name, "category": category}
        )


def seed_default_roles(shop):
    """Create the system default roles for a newly created shop."""
    sync_permission_catalog()
    perms_by_code = {p.code: p for p in Permission.objects.all()}
    for role_type, codes in DEFAULT_ROLE_PERMISSIONS.items():
        role, _ = Role.objects.get_or_create(
            shop=shop, role_type=role_type,
            defaults={"is_system": True, "name": role_type.label},
        )
        role.permissions.set([perms_by_code[c] for c in codes if c in perms_by_code])
    # Owner role row (empty perms; owner bypasses checks) for completeness.
    Role.objects.get_or_create(
        shop=shop, role_type=RoleType.OWNER,
        defaults={"is_system": True, "name": RoleType.OWNER.label},
    )
