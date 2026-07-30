"""
Base DRF viewset for tenant-scoped resources.

Critical: DRF authentication (JWT) runs during ``initial()``, AFTER the
TenantMiddleware has already run. So for API requests the thread-local tenant
must be (re)set here, once ``request.user`` is authenticated. Without this,
``TenantManager`` would fail-closed and every list endpoint would return empty.
"""
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.filters import OrderingFilter

from .permissions import HasPermCode, IsTenantMember
from .tenant_context import set_current_tenant


class TenantScopedViewSet(viewsets.ModelViewSet):
    """
    ModelViewSet that binds the current tenant from the authenticated user and
    enforces tenant membership + optional per-view RBAC (``required_perm``).

    Because ``TenantScopedModel.objects`` already auto-filters by the
    thread-local tenant, subclasses can use ``Model.objects.all()`` safely.
    """

    permission_classes = [IsTenantMember, HasPermCode]
    # Stable default ordering so PageNumberPagination doesn't yield inconsistent
    # / overlapping pages on an unordered queryset (a real correctness bug under
    # load). OrderingFilter also lets clients pass ?ordering=<field> to sort.
    filter_backends = [OrderingFilter]
    ordering = ["-id"]
    required_perm: str | None = None
    feature_flag: str | None = None

    def initial(self, request, *args, **kwargs):
        # Re-resolve tenant now that JWT auth has populated request.user.
        shop = getattr(request.user, "shop", None)
        request.tenant = shop
        set_current_tenant(shop)
        super().initial(request, *args, **kwargs)

    def perform_create(self, serializer):
        # Stamp the tenant on create; never trust a client-supplied shop.
        shop = getattr(self.request.user, "shop", None)
        if shop is None:
            raise PermissionDenied("No tenant bound to this user.")
        serializer.save(shop=shop)
