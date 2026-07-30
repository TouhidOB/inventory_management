"""
Shared logic for supplier + customer dues.

Decision (Phase 0): dues are a single cached ``due_balance`` field on the party
(running balance, option a), NOT a child ledger table. Each row upserts the
party (match phone → name, tenant-scoped) and overwrites its ``due_balance``.
Previous values are snapshotted for rollback.
"""
from decimal import Decimal

from .base import BaseImporter
from .fields import Field


class PartyDuesImporter(BaseImporter):
    MATCH_KEY = "phone"          # primary match; falls back to name
    model = None                 # set by subclass (Supplier / Customer)
    duplicate_in_file = "warning"  # same party twice → last value overwrites

    TARGET_FIELDS = [
        Field("name", "Party name", required=True, kind="text"),
        Field("phone", "Phone", required=False, kind="text"),
        Field("email", "Email", required=False, kind="text"),
        Field("address", "Address", required=False, kind="text"),
        Field("due_amount", "Due amount", required=True, kind="money"),
    ]

    def extra_validate(self, cleaned):
        warnings = []
        due = cleaned.get("due_amount")
        if due is not None and due < 0:
            warnings.append("Due amount is negative")
        return warnings

    def match_value(self, cleaned):
        # Dedupe within a file on normalized phone, else name.
        phone = self.norm_phone(cleaned.get("phone"))
        if phone:
            return f"P:{phone}"
        return f"N:{(cleaned.get('name') or '').strip().upper()}"

    def match(self, cleaned, shop):
        qs = self.model.all_objects.filter(shop_id=shop.id)
        phone = self.norm_phone(cleaned.get("phone"))
        if phone:
            # Normalized-digit match, tolerant of formatting differences.
            for cand in qs.exclude(phone=""):
                if self.norm_phone(cand.phone) == phone:
                    return cand
        name = (cleaned.get("name") or "").strip()
        if name:
            return qs.filter(name__iexact=name).first()
        return None

    def upsert(self, cleaned, existing, shop, job):
        obj = existing or self.model(shop=shop)
        obj.shop = shop
        if not existing:
            obj.name = cleaned.get("name") or ""
        # Fill blank contact fields only; never silently overwrite non-empty ones
        # (a differing value is surfaced as a warning at validate time upstream).
        for src, attr in (("phone", "phone"), ("email", "email"), ("address", "address")):
            val = cleaned.get(src)
            if val and not getattr(obj, attr, ""):
                setattr(obj, attr, val)
        obj.due_balance = cleaned.get("due_amount") or Decimal("0")
        obj.save()
        self._assert_tenant(obj, shop)
        return ("updated" if existing else "created"), obj

    def snapshot_previous(self, existing):
        if existing is None:
            return None
        return {
            "due_balance": str(existing.due_balance),
            "phone": existing.phone,
            "email": existing.email,
            "address": existing.address,
        }

    def get_model(self):
        return self.model

    def restore_snapshot(self, obj, prev):
        obj.due_balance = Decimal(prev["due_balance"])
        obj.phone = prev["phone"]
        obj.email = prev["email"]
        obj.address = prev["address"]
        obj.save()
