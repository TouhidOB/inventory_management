"""
Frontend access guards.

Every shop page requires: (1) authentication, (2) membership in a shop, and
(3) — where marked — a specific RBAC permission. Tenant scoping itself comes
from TenantMiddleware (session auth populates request.user before it runs), but
we set the thread-local defensively here too and use scoped managers in views,
so one shop can never see another's rows even by guessing an object id.
"""
from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied

from core.tenant_context import set_current_tenant


def shop_member_required(view):
    """Authenticated user that belongs to a shop. Platform staff are bounced to
    the Django admin (the tenant frontend is not for them)."""

    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if user.shop_id is None:
            # Platform staff / users without a shop have no tenant frontend.
            raise PermissionDenied("This account is not attached to a shop.")
        # Defensive: ensure tenant scoping is bound for this request.
        request.tenant = user.shop
        set_current_tenant(user.shop)
        return view(request, *args, **kwargs)

    return _wrapped


def platform_staff_required(view):
    """Platform super-admin only (is_staff). During impersonation the request
    user is a shop owner (not staff), so platform pages are correctly locked
    until the admin exits impersonation."""

    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not user.is_staff:
            raise PermissionDenied("Platform staff only.")
        return view(request, *args, **kwargs)

    return _wrapped


def perm_required(code):
    """Require a feature-level RBAC permission (owner bypasses)."""

    def decorator(view):
        @wraps(view)
        def _wrapped(request, *args, **kwargs):
            if not request.user.has_perm_code(code):
                raise PermissionDenied(f"You lack the '{code}' permission.")
            return view(request, *args, **kwargs)

        return _wrapped

    return decorator
