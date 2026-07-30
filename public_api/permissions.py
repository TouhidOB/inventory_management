"""Scope enforcement + throttling for the public API (9.7)."""
from rest_framework.permissions import BasePermission
from rest_framework.throttling import SimpleRateThrottle

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class HasResourceScope(BasePermission):
    """
    Checks the API key allows the view's ``resource`` for the request method.
    The view declares ``resource`` (e.g. "products", "sales").
    """

    message = "This API key lacks the required scope."

    def has_permission(self, request, view):
        api_key = getattr(request, "auth", None)
        resource = getattr(view, "resource", None)
        if api_key is None or resource is None:
            return False
        write = request.method not in SAFE_METHODS
        return api_key.allows(resource, write=write)


class APIKeyRateThrottle(SimpleRateThrottle):
    """Per-key throttle; rate depends on the key's tier."""

    scope = "public_api"  # default so __init__/get_rate works; overridden per key

    def get_cache_key(self, request, view):
        api_key = getattr(request, "auth", None)
        if api_key is None:
            return None
        tier = "public_api_enterprise" if api_key.rate_tier == "enterprise" else "public_api"
        self.scope = tier
        self.rate = self.get_rate()
        return f"throttle_apikey_{api_key.pk}"
