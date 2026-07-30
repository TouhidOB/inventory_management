"""
API-key authentication + tenant resolution for the public API (9.7).

Security posture:
- Tenant is derived ONLY from the key. Any client-supplied shop id is ignored.
- The key must belong to an active shop whose plan includes `api_access`
  (Enterprise gating).
- The resolved tenant is pushed into the thread-local so the same
  TenantScopedModel auto-filtering used everywhere applies to the public API.
"""
from rest_framework import authentication, exceptions

from core.tenant_context import set_current_tenant

from .models import APIKey, hash_key


class APIKeyUser:
    """Lightweight principal representing an authenticated API key (not a User)."""

    is_authenticated = True

    def __init__(self, api_key):
        self.api_key = api_key
        self.shop = api_key.shop
        self.shop_id = api_key.shop_id

    def __str__(self):
        return f"apikey:{self.api_key.prefix}"


class APIKeyAuthentication(authentication.BaseAuthentication):
    keyword = "Api-Key"

    def authenticate(self, request):
        raw = request.META.get("HTTP_X_API_KEY")
        if not raw:
            header = authentication.get_authorization_header(request).decode()
            if header.startswith(self.keyword + " "):
                raw = header[len(self.keyword) + 1:].strip()
        if not raw:
            return None  # let other authenticators / permission run

        api_key = APIKey.objects.select_related("shop", "shop__plan").filter(
            key_hash=hash_key(raw), is_active=True
        ).first()
        if api_key is None:
            raise exceptions.AuthenticationFailed("Invalid API key.")

        shop = api_key.shop
        if not shop.is_active:
            raise exceptions.AuthenticationFailed("Shop is not active.")
        if not shop.has_feature("api_access"):
            raise exceptions.AuthenticationFailed("Public API requires the Enterprise plan.")

        api_key.touch()
        # Bind tenant for auto-scoping.
        request.tenant = shop
        set_current_tenant(shop)
        return (APIKeyUser(api_key), api_key)
