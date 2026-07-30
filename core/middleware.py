"""
Tenant-resolution middleware.

Resolves the current tenant (Shop) for each request and stores it in the
thread-local context so ``TenantManager`` auto-filters queries. Resolution
order:

1. Active impersonation session (platform admin "login as shop owner").
2. The authenticated user's ``shop`` FK.
3. (Hook) subdomain/slug lookup — stubbed for later when host routing lands.

The thread-local is always cleared at the end of the request so a worker
thread never carries a stale tenant into the next request.
"""
from django.utils.deprecation import MiddlewareMixin

from .tenant_context import clear_current_tenant, set_current_tenant

IMPERSONATE_SESSION_KEY = "impersonate_shop_id"

# Content-Security-Policy. Script sources are pinned to self + the known CDNs, so
# a compromised/injected external script URL can't load, and connect-src 'self'
# stops any injected code from exfiltrating to an attacker host. 'unsafe-inline'
# + 'unsafe-eval' are required because the UI uses inline <script> blocks and
# Alpine.js, which compiles its x-* / @click expressions with the Function
# constructor (blocked without 'unsafe-eval' — that omission silently disables
# every Alpine interaction: sidebar, modals, POS). The source-allowlist +
# connect-src still meaningfully reduce blast radius (defense-in-depth on top of
# Django autoescaping).
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://unpkg.com; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://img.youtube.com https://i.ytimg.com; "
    "connect-src 'self'; "
    "frame-src https://www.youtube.com https://www.youtube-nocookie.com; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


class ContentSecurityPolicyMiddleware(MiddlewareMixin):
    """Attach a CSP header to every response (skips the DRF/Swagger docs, which
    need inline eval for the schema UI)."""

    def process_response(self, request, response):
        if request.path.startswith("/api/docs") or request.path.startswith("/api/redoc"):
            return response
        response.setdefault("Content-Security-Policy", _CSP)
        return response


class TenantMiddleware(MiddlewareMixin):
    def process_request(self, request):
        request.tenant = None
        request.is_impersonating = False

        user = getattr(request, "user", None)

        # 1. Impersonation takes precedence (audited elsewhere).
        session = getattr(request, "session", None)
        if session is not None and session.get(IMPERSONATE_SESSION_KEY):
            from tenants.models import Shop

            shop = Shop.objects.filter(
                pk=session[IMPERSONATE_SESSION_KEY]
            ).first()
            if shop is not None:
                request.tenant = shop
                request.is_impersonating = True

        # 2. Normal case: tenant from the authenticated user.
        if request.tenant is None and user is not None and user.is_authenticated:
            shop = getattr(user, "shop", None)
            if shop is not None:
                request.tenant = shop

        set_current_tenant(request.tenant)
        return None

    def process_response(self, request, response):
        clear_current_tenant()
        return response

    def process_exception(self, request, exception):
        clear_current_tenant()
        return None
