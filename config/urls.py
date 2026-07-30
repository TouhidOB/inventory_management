"""Root URL configuration for StockWhisk."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import HttpResponse, JsonResponse
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)


def health(_request):
    return JsonResponse({"status": "ok", "service": "stockwhisk"})


def manifest(_request):
    """PWA manifest (9.6)."""
    return JsonResponse({
        "name": "StockWhisk",
        "short_name": "StockWhisk",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#111827",
        "theme_color": "#1f2937",
        "icons": [],
    })


SERVICE_WORKER_JS = """
// StockWhisk PWA shell cache (9.6). POS needs connectivity for stock accuracy,
// so we cache only the app shell, never transactional data.
const CACHE = 'stockwhisk-shell-v1';
const SHELL = ['/'];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)));
});
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith('/api/')) return; // never cache API calls
  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
});
"""


def service_worker(_request):
    return HttpResponse(SERVICE_WORKER_JS, content_type="application/javascript")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz/", health, name="health"),
    # PWA (9.6)
    path("manifest.json", manifest, name="manifest"),
    path("sw.js", service_worker, name="service-worker"),
    # API docs (9.6)
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path("api/", include("accounts.urls")),
    path("api/platform/", include("platform_admin.urls")),
    # Phase 1 business APIs
    path("api/catalog/", include("catalog.urls")),
    path("api/inventory/", include("inventory.urls")),
    path("api/crm/", include("crm.urls")),
    path("api/purchasing/", include("purchasing.urls")),
    path("api/sales/", include("sales.urls")),
    path("api/pos/", include("pos.urls")),
    path("api/accounting/", include("accounting.urls")),
    path("api/analytics/", include("analytics.urls")),
    path("api/billing/", include("billing.urls")),
    # Phase 2
    path("api/notifications/", include("notifications.urls")),
    path("api/branches/", include("branches.urls")),
    path("api/reports/", include("reports.urls")),
    # Phase 3
    path("api/service/", include("service.urls")),
    path("api/v1/", include("public_api.urls")),  # Enterprise public API
    # Super Admin dashboard (server-rendered, staff only)
    path("platform/imports/", include("imports.urls")),
    path("platform/", include("platform_admin.web_urls")),
    # Server-rendered shop frontend (mounted at root; keep last)
    path("", include("web.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
