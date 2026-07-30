"""The single most important guarantee: no cross-tenant data leakage."""
import pytest

from core.tenant_context import (
    bypass_tenant_scope,
    set_current_tenant,
    tenant_context,
)

pytestmark = pytest.mark.django_db


def test_manager_fails_closed_without_tenant(widget_model, two_shops):
    (shop_a, _), _ = two_shops
    with tenant_context(shop_a):
        widget_model.objects.create(name="w1")
    # No tenant set -> fail-closed empty queryset (never leak).
    set_current_tenant(None)
    assert widget_model.objects.count() == 0


def test_queries_are_scoped_to_current_tenant(widget_model, two_shops):
    (shop_a, _), (shop_b, _) = two_shops
    with tenant_context(shop_a):
        widget_model.objects.create(name="a1")
        widget_model.objects.create(name="a2")
    with tenant_context(shop_b):
        widget_model.objects.create(name="b1")

    with tenant_context(shop_a):
        names = set(widget_model.objects.values_list("name", flat=True))
        assert names == {"a1", "a2"}
    with tenant_context(shop_b):
        names = set(widget_model.objects.values_list("name", flat=True))
        assert names == {"b1"}


def test_auto_stamps_tenant_on_create(widget_model, two_shops):
    (shop_a, _), _ = two_shops
    with tenant_context(shop_a):
        w = widget_model.objects.create(name="auto")
        assert w.shop_id == shop_a.id


def test_bypass_sees_all_tenants(widget_model, two_shops):
    (shop_a, _), (shop_b, _) = two_shops
    with tenant_context(shop_a):
        widget_model.objects.create(name="a1")
    with tenant_context(shop_b):
        widget_model.objects.create(name="b1")
    with bypass_tenant_scope():
        assert widget_model.objects.count() == 2
