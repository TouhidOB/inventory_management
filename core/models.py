"""Shared abstract base models and the tenant-scoping manager."""
from django.db import models

from .tenant_context import (
    get_current_tenant,
    get_current_tenant_id,
    get_tenant_filter_bypass,
)


class TimeStampedModel(models.Model):
    """Adds self-managing ``created_at`` / ``updated_at`` to any model."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class TenantQuerySet(models.QuerySet):
    def for_tenant(self, shop):
        """Explicitly scope to a shop (ignores thread-local)."""
        return self.filter(shop=shop)


class TenantManager(models.Manager):
    """
    Default manager that auto-filters every queryset by the current tenant
    resolved from the thread-local context.

    - If ``bypass_tenant_scope()`` is active (platform admin), no filtering.
    - If no tenant is set, returns an EMPTY queryset. This is a deliberate
      fail-closed default: forgetting to set the tenant must never leak another
      shop's data. Trusted cross-tenant code must opt in via the bypass.
    """

    def get_queryset(self):
        qs = TenantQuerySet(self.model, using=self._db)
        if get_tenant_filter_bypass():
            return qs
        tenant_id = get_current_tenant_id()
        if tenant_id is None:
            return qs.none()
        return qs.filter(shop_id=tenant_id)

    def for_tenant(self, shop):
        return self.get_queryset().for_tenant(shop)


class TenantScopedModel(TimeStampedModel):
    """
    Abstract base for every tenant-owned model.

    Carries the ``shop`` FK (the tenant) and swaps in the auto-filtering
    manager as the default ``objects``. ``all_objects`` is an unscoped escape
    hatch for migrations, platform admin, and analytics that already handle
    isolation themselves.
    """

    shop = models.ForeignKey(
        "tenants.Shop",
        on_delete=models.CASCADE,
        related_name="%(class)ss",
        db_index=True,
        editable=False,
    )

    objects = TenantManager()
    all_objects = models.Manager()  # unscoped; use with care

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        # Auto-stamp the tenant on create when it wasn't set explicitly, using
        # the thread-local context. Prevents "forgot to set shop" bugs.
        if self.shop_id is None:
            tenant = get_current_tenant()
            if tenant is not None:
                self.shop_id = get_current_tenant_id()
        super().save(*args, **kwargs)
