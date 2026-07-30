"""
Platform super-admin gate for the import pipeline.

The real super-admin signal in this project is ``user.is_staff`` (see
``web.guards.platform_staff_required`` and ``platform_admin``). We reuse it here
rather than assuming ``is_superuser``.
"""
from functools import wraps

from django.http import HttpResponseForbidden
from rest_framework.permissions import BasePermission


def is_platform_superadmin(user):
    return bool(user and user.is_authenticated and user.is_staff)


class IsPlatformSuperAdmin(BasePermission):
    """DRF permission — platform super admin only."""

    message = "Platform super admin required."

    def has_permission(self, request, view):
        return is_platform_superadmin(request.user)


def platform_superadmin_required(view):
    """Decorator for server-rendered (HTMX) import views."""

    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if not is_platform_superadmin(request.user):
            return HttpResponseForbidden("Platform super admin required.")
        return view(request, *args, **kwargs)

    return _wrapped
