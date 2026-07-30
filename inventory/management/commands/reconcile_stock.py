"""
Reconcile cached ``current_stock`` against the immutable movement ledger.

The hot write path (`inventory.services._apply_stock_delta`) nudges the cache by
each movement's delta in O(1). This command is the authoritative safety net:
run it periodically (e.g. nightly cron) to re-sum the ledger and correct any
drift. Reports products whose cached value differed.

Usage:
    python manage.py reconcile_stock            # all shops, fix drift
    python manage.py reconcile_stock --dry-run  # report only, no writes
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Sum

from catalog.models import Product
from inventory.models import StockMovement


class Command(BaseCommand):
    help = "Re-sum the stock ledger and correct any drift in Product.current_stock."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report drift only.")

    def handle(self, *args, **opts):
        dry = opts["dry_run"]
        # One aggregate query for the whole ledger, grouped by product.
        totals = {
            row["product_id"]: row["s"] or Decimal("0")
            for row in StockMovement.all_objects.values("product_id").annotate(s=Sum("quantity"))
        }
        drift = 0
        for p in Product.all_objects.all().iterator():
            expected = totals.get(p.id, Decimal("0"))
            if Decimal(p.current_stock or 0) != expected:
                drift += 1
                self.stdout.write(f"drift product#{p.id} {p.current_stock} -> {expected}")
                if not dry:
                    Product.all_objects.filter(pk=p.id).update(current_stock=expected)
        verb = "would fix" if dry else "fixed"
        self.stdout.write(self.style.SUCCESS(f"Done. {verb} {drift} product(s) with drift."))
