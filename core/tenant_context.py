"""
Thread-local tenant context.

Multi-tenancy strategy: shared database, shared schema, scoped by ``shop_id``
(the tenant FK) on every tenant-owned model.

The "current tenant" for a request/task is stored in a thread-local so that the
custom ``TenantManager`` can auto-filter querysets without every call site
having to pass the shop explicitly. The middleware sets this from the
authenticated user; Celery tasks / management commands must set it explicitly
via :func:`set_current_tenant` (or the :func:`tenant_context` context manager).

WARNING: thread-locals are per-OS-thread. Under ASGI/async or greenlet-based
servers this needs revisiting (contextvars). For the WSGI + Celery prefork
deployment target this is correct and simple.
"""
import threading
from contextlib import contextmanager

_state = threading.local()


def set_current_tenant(shop):
    """Set the active tenant (a Shop instance or its pk) for this thread."""
    _state.tenant = shop


def get_current_tenant():
    """Return the active tenant for this thread, or ``None`` if unset."""
    return getattr(_state, "tenant", None)


def get_current_tenant_id():
    tenant = get_current_tenant()
    if tenant is None:
        return None
    return getattr(tenant, "pk", tenant)


def clear_current_tenant():
    _state.tenant = None


@contextmanager
def tenant_context(shop):
    """Temporarily run a block scoped to ``shop`` (Celery tasks, admin, tests)."""
    previous = get_current_tenant()
    set_current_tenant(shop)
    try:
        yield
    finally:
        set_current_tenant(previous)


# --- Bypass switch -----------------------------------------------------------
# The platform (super) admin and cross-tenant analytics legitimately need to see
# every tenant's rows. This flag lets trusted code opt out of auto-filtering.
# It is deliberately verbose to make audits easy to grep for.

def set_tenant_filter_bypass(value: bool):
    _state.bypass = bool(value)


def get_tenant_filter_bypass() -> bool:
    return getattr(_state, "bypass", False)


@contextmanager
def bypass_tenant_scope():
    """Run a block with tenant auto-filtering disabled (platform admin only)."""
    previous = get_tenant_filter_bypass()
    set_tenant_filter_bypass(True)
    try:
        yield
    finally:
        set_tenant_filter_bypass(previous)
