"""
Subscription lifecycle Celery task (8.7 step 6).

Daily: send trial-expiry reminders (7/3/1 days), move ended subscriptions into
grace period, then to expired (feature-restricted) once grace lapses without an
approved ManualPayment. No charging — purely state + notifications.
"""
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from notifications.models import NotificationType
from notifications.services import notify
from tenants.models import Shop, Subscription

from .services import GRACE_DAYS


@shared_task
def check_subscription_expiry():
    now = timezone.now()
    today = now.date()
    stats = {"reminders": 0, "grace": 0, "expired": 0}

    # Trial reminders
    for shop in Shop.objects.filter(is_active=True, trial_ends_at__isnull=False):
        days_left = (shop.trial_ends_at.date() - today).days
        if days_left in (7, 3, 1):
            notify(
                shop=shop, type=NotificationType.SUBSCRIPTION,
                title=f"Trial ends in {days_left} day(s)",
                message="Submit a manual payment to keep your subscription active.",
                email=True,
            )
            stats["reminders"] += 1

    # Period-end transitions
    subs = Subscription.objects.filter(
        is_current=True,
        status__in=[Subscription.Status.ACTIVE, Subscription.Status.TRIALING,
                    Subscription.Status.GRACE_PERIOD],
        current_period_end__isnull=False,
    )
    for sub in subs.select_related("shop"):
        end = sub.current_period_end
        if end >= now:
            continue
        if sub.status != Subscription.Status.GRACE_PERIOD:
            sub.status = Subscription.Status.GRACE_PERIOD
            sub.save(update_fields=["status"])
            notify(shop=sub.shop, type=NotificationType.SUBSCRIPTION,
                   title="Subscription in grace period",
                   message=f"Please pay within {GRACE_DAYS} days to avoid restriction.",
                   email=True)
            stats["grace"] += 1
        elif now - end > timedelta(days=GRACE_DAYS):
            sub.status = Subscription.Status.EXPIRED
            sub.save(update_fields=["status"])
            # Feature-restrict by deactivating the shop's active flag.
            sub.shop.is_active = False
            sub.shop.save(update_fields=["is_active"])
            notify(shop=sub.shop, type=NotificationType.SUBSCRIPTION,
                   title="Subscription expired",
                   message="Access is now restricted. Submit a payment to reactivate.",
                   email=True)
            stats["expired"] += 1
    return stats
