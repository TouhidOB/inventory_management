"""Daily warranty-expiry scan (9.1)."""
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from notifications.models import NotificationType
from notifications.services import notify
from tenants.models import Shop

from .models import Warranty
from .services import refresh_warranty_statuses


@shared_task
def scan_warranty_expiry(window_days=30):
    now = timezone.localdate()
    cutoff = now + timedelta(days=window_days)
    stats = {"shops_notified": 0}
    for shop in Shop.objects.filter(is_active=True):
        refresh_warranty_statuses(shop, soon_days=window_days)
        expiring = Warranty.all_objects.filter(
            shop_id=shop.id, expiry_date__gte=now, expiry_date__lte=cutoff,
        ).exclude(status__in=[Warranty.Status.CLAIMED, Warranty.Status.VOID])
        count = expiring.count()
        if count:
            notify(
                shop=shop, type=NotificationType.GENERAL,
                title=f"{count} warranties expiring within {window_days} days",
                message="Review expiring warranties from the service module.",
                metadata={"warranty_ids": list(expiring.values_list("id", flat=True))},
                email=True,
            )
            stats["shops_notified"] += 1
    return stats
