"""Celery tasks for recurring expenses (8.6)."""
from celery import shared_task
from django.utils import timezone

from .models import RecurringExpense
from .services import record_expense


@shared_task
def generate_recurring_expenses():
    """Materialize each active recurring expense once per calendar month."""
    now = timezone.now()
    period = now.strftime("%Y-%m")
    created = 0
    for rec in RecurringExpense.all_objects.filter(is_active=True).exclude(last_generated_period=period):
        day = min(rec.day_of_month or 1, 28)
        spent_on = now.date().replace(day=day)
        # Reconstruct a lightweight shop stub for the service.
        from tenants.models import Shop
        shop = Shop.objects.filter(pk=rec.shop_id).first()
        if shop is None:
            continue
        record_expense(
            shop=shop, amount=rec.amount, spent_on=spent_on,
            category=rec.category, payment_method=rec.payment_method,
            note=f"Recurring: {rec.label}",
        )
        rec.last_generated_period = period
        rec.save(update_fields=["last_generated_period"])
        created += 1
    return {"expenses_created": created}
