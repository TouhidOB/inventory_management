"""Product importer — name / sku / cost (+ optional selling price, barcode).

Stock qty is intentionally NOT imported: ``Product.current_stock`` is a cached
value derived from the append-only StockMovement ledger, so the owner receives
opening stock via a PO. See the Phase 0 discovery decision.
"""
from decimal import Decimal

from .base import BaseImporter
from .fields import Field


class ProductImporter(BaseImporter):
    import_type = "products"
    MATCH_KEY = "sku_code"
    TARGET_FIELDS = [
        Field("name", "Item name", required=True, kind="text"),
        Field("sku_code", "SKU code", required=True, kind="text"),
        Field("avg_cost", "Avg cost", required=True, kind="money"),
        Field("selling_price", "Selling price", required=False, kind="money"),
        Field("barcode", "Barcode", required=False, kind="text"),
    ]

    def extra_validate(self, cleaned):
        warnings = []
        cost = cleaned.get("avg_cost")
        if cost is not None and cost < 0:
            warnings.append("Avg cost is negative")
        elif cost is not None and cost < Decimal("1"):
            warnings.append("Avg cost is suspiciously low (< 1)")
        sell = cleaned.get("selling_price")
        if sell is not None and sell < 0:
            warnings.append("Selling price is negative")
        return warnings

    def match(self, cleaned, shop):
        from catalog.models import Product
        sku = (cleaned.get("sku_code") or "").strip()
        if not sku:
            return None
        return (
            Product.all_objects.filter(shop_id=shop.id, sku__iexact=sku).first()
        )

    def upsert(self, cleaned, existing, shop, job):
        from catalog.models import Product
        sku = (cleaned.get("sku_code") or "").strip()
        cost = cleaned.get("avg_cost") or Decimal("0")
        obj = existing or Product(shop=shop)
        obj.shop = shop
        obj.name = cleaned.get("name") or obj.name
        obj.sku = sku
        obj.cost_price = cost
        if cleaned.get("selling_price") is not None:
            obj.selling_price = cleaned["selling_price"]
        if cleaned.get("barcode"):
            obj.barcode = cleaned["barcode"]
        obj.save()
        self._assert_tenant(obj, shop)
        return ("updated" if existing else "created"), obj

    def get_model(self):
        from catalog.models import Product
        return Product

    def restore_snapshot(self, obj, prev):
        obj.name = prev["name"]
        obj.sku = prev["sku"]
        obj.cost_price = Decimal(prev["cost_price"])
        obj.selling_price = Decimal(prev["selling_price"])
        obj.barcode = prev["barcode"]
        obj.save()

    def snapshot_previous(self, existing):
        """Field values captured before an update, for rollback."""
        if existing is None:
            return None
        return {
            "name": existing.name,
            "sku": existing.sku,
            "cost_price": str(existing.cost_price),
            "selling_price": str(existing.selling_price),
            "barcode": existing.barcode,
        }
