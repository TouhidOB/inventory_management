"""
Shared fixtures. Defines a throwaway concrete ``TenantScopedModel`` subclass
and creates its DB table via schema_editor so tenant auto-filtering can be
tested end-to-end without shipping a demo model in production.
"""
import pytest
from django.core.cache import cache
from django.db import connection, models

from core.models import TenantScopedModel


@pytest.fixture(autouse=True)
def _clear_cache():
    """Isolate tests: the analytics cache (LocMem) survives DB rollback, and
    test shop ids repeat across tests, so a cached result could bleed between
    tests. Clear before each."""
    cache.clear()
    yield


class ScopedWidget(TenantScopedModel):
    """Test-only tenant-scoped model to exercise TenantManager."""

    name = models.CharField(max_length=50)

    class Meta:
        app_label = "audit"  # borrow an installed app label


@pytest.fixture(scope="session")
def _widget_table(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock():
        with connection.schema_editor() as schema:
            schema.create_model(ScopedWidget)
        yield
        with connection.schema_editor() as schema:
            schema.delete_model(ScopedWidget)


@pytest.fixture
def widget_model(_widget_table):
    return ScopedWidget


@pytest.fixture
def plan(db):
    from tenants.models import SubscriptionPlan
    return SubscriptionPlan.objects.create(
        name="Pro", tier=SubscriptionPlan.Tier.PROFESSIONAL,
        features={"advanced_analytics": True, "multi_branch": True},
    )


@pytest.fixture
def two_shops(db, plan):
    from tenants.services import register_shop
    shop_a, owner_a = register_shop(
        name="Alpha Electronics", owner_email="a@ex.com",
        owner_password="pass12345", plan=plan,
    )
    shop_b, owner_b = register_shop(
        name="Beta Mobiles", owner_email="b@ex.com",
        owner_password="pass12345", plan=plan,
    )
    return (shop_a, owner_a), (shop_b, owner_b)
