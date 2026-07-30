"""Tenant provisioning: register a shop with its owner in one transaction."""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from accounts.models import RoleType, User
from accounts.rbac import seed_default_roles
from audit.models import AuditLog
from audit.services import record

from .models import Branch, Shop, Subscription, SubscriptionPlan

TRIAL_DAYS = 14


@transaction.atomic
def register_shop(
    *, name, owner_email, owner_password, owner_name="",
    business_type=Shop.BusinessType.GENERAL, phone="", plan=None,
):
    """
    Provision a new tenant:
    1. Create the Shop on the Free/default plan with a trial window.
    2. Create a main Branch.
    3. Seed system roles + permission catalog.
    4. Create the Owner user.
    5. Open a trialing Subscription.
    6. Audit the creation.
    """
    if plan is None:
        plan = (
            SubscriptionPlan.objects.filter(tier=SubscriptionPlan.Tier.FREE).first()
            or SubscriptionPlan.objects.filter(is_active=True).order_by("price_monthly").first()
        )

    now = timezone.now()
    shop = Shop.objects.create(
        name=name,
        business_type=business_type,
        phone=phone,
        plan=plan,
        trial_ends_at=now + timedelta(days=TRIAL_DAYS),
    )

    Branch.objects.create(shop=shop, name="Main", is_main=True)
    seed_default_roles(shop)

    from accounting.services import seed_expense_categories
    seed_expense_categories(shop)

    first, _, last = owner_name.partition(" ")
    owner = User.objects.create_user(
        email=owner_email,
        password=owner_password,
        shop=shop,
        role=RoleType.OWNER,
        first_name=first,
        last_name=last,
    )

    if plan is not None:
        Subscription.objects.create(
            shop=shop, plan=plan,
            status=Subscription.Status.TRIALING,
            current_period_end=shop.trial_ends_at,
        )

    record(
        action=AuditLog.Action.CREATE, actor=owner, shop=shop, target=shop,
        description=f"Shop '{shop.name}' registered",
    )
    return shop, owner
