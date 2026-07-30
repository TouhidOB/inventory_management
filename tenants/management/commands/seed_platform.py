"""
Seed baseline platform data: the single subscription plan + RBAC catalog.
Idempotent — safe to run on every deploy.

Pricing model: ONE flat package at 799/month with every feature included.
Any older plans are deactivated (kept for FK integrity) so only this one shows.
"""
from django.core.management.base import BaseCommand

from accounts.rbac import sync_permission_catalog
from billing.models import SubscriptionInvoice
from tenants.models import Shop, Subscription, SubscriptionPlan

# The one and only package.
SINGLE_PLAN = {
    "tier": SubscriptionPlan.Tier.PROFESSIONAL,
    "name": "StockWhisk",
    "price_monthly": 799,
    "price_yearly": 9588,  # 799 × 12
    "max_users": 1000,
    "max_branches": 1000,
    "max_products": 1000000,
    "features": {
        "pos": True, "basic_analytics": True, "advanced_analytics": True,
        "reports_export": True, "multi_branch": True,
        "api_access": True,
    },
    "is_active": True,
}


class Command(BaseCommand):
    help = "Seed the single subscription plan (flat 799) and the RBAC catalog."

    def handle(self, *args, **options):
        plan, _ = SubscriptionPlan.objects.update_or_create(
            tier=SINGLE_PLAN["tier"], defaults=SINGLE_PLAN
        )
        # There is exactly ONE plan. Repoint anything on an older plan to this
        # one (FKs are PROTECT), then delete the rest so only this plan exists.
        others = SubscriptionPlan.objects.exclude(pk=plan.pk)
        if others.exists():
            # Use unscoped managers — some of these are tenant-scoped and the
            # command runs outside any tenant context.
            for model in (Shop, Subscription, SubscriptionInvoice):
                mgr = getattr(model, "all_objects", model._default_manager)
                mgr.filter(plan__in=others).update(plan=plan)
            removed = others.count()
            others.delete()
            self.stdout.write(self.style.SUCCESS(f"Removed {removed} old plan(s)."))
        self.stdout.write(self.style.SUCCESS("Seeded single plan 'StockWhisk' @ 799/mo."))
        sync_permission_catalog()
        self.stdout.write(self.style.SUCCESS("Synced permission catalog."))
