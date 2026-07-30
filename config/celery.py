"""Celery entrypoint. Import side-effect wires the app on Django startup."""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("stockwhisk")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Periodic tasks (Phase 2). Times are in CELERY_TIMEZONE.
app.conf.beat_schedule = {
    "scan-low-stock-hourly": {
        "task": "inventory.tasks.scan_low_stock",
        "schedule": 3600.0,
    },
    "check-subscription-expiry-daily": {
        "task": "billing.tasks.check_subscription_expiry",
        "schedule": 86400.0,
    },
    "generate-recurring-expenses-daily": {
        "task": "accounting.tasks.generate_recurring_expenses",
        "schedule": 86400.0,
    },
    "scan-warranty-expiry-daily": {
        "task": "service.tasks.scan_warranty_expiry",
        "schedule": 86400.0,
    },
    # Nightly reconcile of cached stock against the movement ledger (drift safety
    # net for the O(1) incremental write path).
    "reconcile-stock-daily": {
        "task": "inventory.tasks.reconcile_stock",
        "schedule": 86400.0,
    },
}


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
