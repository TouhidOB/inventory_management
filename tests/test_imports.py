"""
Tests for the super-admin bulk import pipeline (imports app).

Covers: number/text normalization, validation branches, match/upsert +
idempotency, mapping (only-mapped-columns, positional, templates, required-gate),
permissions, tenant isolation, and transactional rollback on mid-commit failure.
"""
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from accounts.models import User
from catalog.models import Product
from core.tenant_context import bypass_tenant_scope
from crm.models import Customer
from imports.importers import get_importer
from imports.importers.base import BaseImporter
from imports.models import ImportMapping, ImportRow
from imports.services import (
    commit_job,
    detect_and_store_columns,
    missing_required_mappings,
    rollback_job,
    stage_job,
)
from purchasing.models import Supplier


@pytest.fixture
def staff(db):
    return User.objects.create_superuser(email="root@plat.com", password="pass12345")


def _job(shop, import_type, content, mapping, staff, filename="f.csv"):
    from imports.models import ImportJob
    job = ImportJob.objects.create(
        shop=shop, import_type=import_type,
        source_file=SimpleUploadedFile(filename, content.encode("utf-8")),
        original_filename=filename, created_by=staff,
    )
    detect_and_store_columns(job)
    job.column_mapping = mapping
    job.save(update_fields=["column_mapping"])
    return job


# --- normalization --------------------------------------------------------

def test_clean_number_strips_currency_and_lakh_and_western_grouping():
    c = BaseImporter.clean_number
    assert c("৳2,96,300.00") == Decimal("296300.00")   # Bengali lakh grouping
    assert c("6,210.00") == Decimal("6210.00")          # Western grouping
    assert c("৳ 1,000") == Decimal("1000")
    assert c("") is None and c("abc") is None


def test_clean_text_collapses_whitespace_and_drops_none_string():
    t = BaseImporter.clean_text
    assert t("Long\n  wrapped   name") == "Long wrapped name"
    assert t("None") == "" and t("  x  ") == "x"


# --- validation -----------------------------------------------------------

def test_product_validation_flags_missing_required_and_low_cost():
    imp = get_importer("products")
    status, errs = imp.validate_row({"name": "", "sku_code": "A1", "avg_cost": Decimal("5")})
    assert status == "error" and any("required" in e.lower() for e in errs)
    status, warns = imp.validate_row({"name": "X", "sku_code": "A1", "avg_cost": Decimal("0.5")})
    assert status == "warning" and warns


# --- mapping: only mapped columns reach cleaned_data ----------------------

def test_only_mapped_columns_reach_cleaned_data(two_shops, staff):
    (shop_a, _), _ = two_shops
    csv = "ITEM NAME,SKU-CODE,AVG.COST,ASSET VALUE,SN\nWidget,W1,৳1,200,999,7\n"
    job = _job(shop_a, "products", csv, {"name": 0, "sku_code": 1, "avg_cost": 2}, staff)
    stage_job(job)
    row = job.rows.get()
    assert set(row.cleaned_data) == {"name", "sku_code", "avg_cost"}
    assert "asset_value" not in row.cleaned_data and "SN" not in row.cleaned_data
    # raw_data keeps the full source row for the audit/error report.
    assert "ASSET VALUE" in row.raw_data


def test_positional_mapping_works_with_nondescriptive_headers(two_shops, staff):
    (shop_a, _), _ = two_shops
    csv = "c1,c2,c3\nGadget,G9,55\n"
    job = _job(shop_a, "products", csv, {"name": 0, "sku_code": 1, "avg_cost": 2}, staff)
    stage_job(job)
    row = job.rows.get()
    assert row.cleaned_data["sku_code"] == "G9" and row.cleaned_data["avg_cost"] == "55"


def test_missing_required_mapping_blocks_preview(two_shops, staff):
    (shop_a, _), _ = two_shops
    csv = "ITEM NAME,SKU-CODE,AVG.COST\nA,A1,5\n"
    job = _job(shop_a, "products", csv, {"name": 0}, staff)  # sku + cost unmapped
    assert set(missing_required_mappings(job)) == {"SKU code", "Avg cost"}


def test_import_mapping_template_reapplies(two_shops, staff):
    (shop_a, _), _ = two_shops
    tpl = ImportMapping.objects.create(
        shop=shop_a, import_type="products", name="Std",
        mapping={"name": 0, "sku_code": 1, "avg_cost": 2}, created_by=staff,
    )
    csv = "ITEM NAME,SKU-CODE,AVG.COST\nA,A1,5\n"
    job = _job(shop_a, "products", csv, tpl.mapping, staff)
    assert missing_required_mappings(job) == []


# --- match / upsert / idempotency -----------------------------------------

def test_products_commit_creates_then_updates_idempotently(two_shops, staff):
    (shop_a, _), _ = two_shops
    csv = 'ITEM NAME,SKU-CODE,AVG.COST,SELL\nWidget,W1,"৳1,200",1500\n'
    mapping = {"name": 0, "sku_code": 1, "avg_cost": 2, "selling_price": 3}
    job = _job(shop_a, "products", csv, mapping, staff)
    stage_job(job)
    commit_job(job, actor=staff)
    with bypass_tenant_scope():
        p = Product.all_objects.get(shop=shop_a, sku="W1")
        assert p.cost_price == Decimal("1200") and p.selling_price == Decimal("1500")
        assert Product.all_objects.filter(shop=shop_a).count() == 1
    assert job.created_count == 1 and job.updated_count == 0

    # Re-run same file → update, no duplicate.
    job2 = _job(shop_a, "products", csv, mapping, staff)
    stage_job(job2)
    assert job2.rows.get().status == ImportRow.Status.WILL_UPDATE
    commit_job(job2, actor=staff)
    with bypass_tenant_scope():
        assert Product.all_objects.filter(shop=shop_a).count() == 1
    assert job2.updated_count == 1 and job2.created_count == 0


def test_supplier_dues_upsert_overwrites_due_balance_and_matches_by_phone(two_shops, staff):
    (shop_a, _), _ = two_shops
    with bypass_tenant_scope():
        Supplier.all_objects.create(shop=shop_a, name="Acme", phone="01700-000", due_balance=0)
    csv = 'SUPPLIER NAME,PHONE,DUE\nAcme Traders,01700 000,"৳9,500"\n'
    mapping = {"name": 0, "phone": 1, "due_amount": 2}
    job = _job(shop_a, "supplier_dues", csv, mapping, staff)
    stage_job(job)
    assert job.rows.get().status == ImportRow.Status.WILL_UPDATE  # matched on normalized phone
    commit_job(job, actor=staff)
    with bypass_tenant_scope():
        s = Supplier.all_objects.get(shop=shop_a, name="Acme")  # name not overwritten
        assert s.due_balance == Decimal("9500")


def test_customer_dues_duplicate_party_in_file_is_warning_last_wins(two_shops, staff):
    (shop_a, _), _ = two_shops
    csv = "NAME,PHONE,DUE\nAli,0170,100\nAli,0170,250\n"
    mapping = {"name": 0, "phone": 1, "due_amount": 2}
    job = _job(shop_a, "customer_dues", csv, mapping, staff)
    stage_job(job)
    r1, r2 = list(job.rows.order_by("row_number"))
    assert r2.errors and "overwrites" in r2.errors[0]
    assert job.error_rows == 0
    commit_job(job, actor=staff)
    with bypass_tenant_scope():
        assert Customer.all_objects.get(shop=shop_a, name="Ali").due_balance == Decimal("250")


def test_duplicate_sku_within_file_is_error(two_shops, staff):
    (shop_a, _), _ = two_shops
    csv = "ITEM NAME,SKU-CODE,AVG.COST\nA,DUP,5\nB,DUP,6\n"
    mapping = {"name": 0, "sku_code": 1, "avg_cost": 2}
    job = _job(shop_a, "products", csv, mapping, staff)
    stage_job(job)
    assert job.error_rows == 1


# --- tenant isolation -----------------------------------------------------

def test_import_into_shop_a_never_touches_shop_b(two_shops, staff):
    (shop_a, _), (shop_b, _) = two_shops
    csv = "ITEM NAME,SKU-CODE,AVG.COST\nWidget,W1,10\n"
    mapping = {"name": 0, "sku_code": 1, "avg_cost": 2}
    job = _job(shop_a, "products", csv, mapping, staff)
    stage_job(job)
    commit_job(job, actor=staff)
    with bypass_tenant_scope():
        assert Product.all_objects.filter(shop=shop_a).count() == 1
        assert Product.all_objects.filter(shop=shop_b).count() == 0


# --- transactional rollback on mid-commit failure -------------------------

def test_midcommit_failure_rolls_back_whole_job(two_shops, staff, monkeypatch):
    (shop_a, _), _ = two_shops
    csv = "ITEM NAME,SKU-CODE,AVG.COST\nGood,OK1,5\nBoom,BOOM,6\n"
    mapping = {"name": 0, "sku_code": 1, "avg_cost": 2}
    job = _job(shop_a, "products", csv, mapping, staff)
    stage_job(job)

    from imports.importers.products import ProductImporter
    original = ProductImporter.upsert

    def flaky(self, cleaned, existing, shop, jb):
        if cleaned.get("name") == "Boom":
            raise RuntimeError("injected failure")
        return original(self, cleaned, existing, shop, jb)

    monkeypatch.setattr(ProductImporter, "upsert", flaky)
    from imports.models import ImportJob
    with pytest.raises(RuntimeError):
        commit_job(job, actor=staff)
    job.refresh_from_db()
    assert job.status == ImportJob.Status.FAILED
    with bypass_tenant_scope():
        assert Product.all_objects.filter(shop=shop_a).count() == 0  # first row rolled back too


# --- rollback of a committed job ------------------------------------------

def test_rollback_deletes_created_and_restores_updated(two_shops, staff):
    (shop_a, _), _ = two_shops
    with bypass_tenant_scope():
        Product.all_objects.create(shop=shop_a, name="Old", sku="UPD", cost_price=Decimal("1"))
    csv = "ITEM NAME,SKU-CODE,AVG.COST\nNew,NEW,10\nOld2,UPD,99\n"
    mapping = {"name": 0, "sku_code": 1, "avg_cost": 2}
    job = _job(shop_a, "products", csv, mapping, staff)
    stage_job(job)
    commit_job(job, actor=staff)
    with bypass_tenant_scope():
        assert Product.all_objects.get(shop=shop_a, sku="UPD").cost_price == Decimal("99")
    rollback_job(job, actor=staff)
    with bypass_tenant_scope():
        assert not Product.all_objects.filter(shop=shop_a, sku="NEW").exists()  # created → deleted
        assert Product.all_objects.get(shop=shop_a, sku="UPD").cost_price == Decimal("1")  # restored


# --- permissions ----------------------------------------------------------

def test_permissions_superadmin_only(client, two_shops, staff):
    (shop_a, owner_a), _ = two_shops
    # Anonymous → 403.
    assert client.get("/platform/imports/").status_code in (302, 403)
    # Tenant owner → 403.
    client.login(email=owner_a.email, password="pass12345")
    assert client.get("/platform/imports/").status_code == 403
    client.logout()
    # Super admin → 200.
    client.login(email="root@plat.com", password="pass12345")
    assert client.get("/platform/imports/").status_code == 200


def test_full_web_wizard_upload_map_preview_commit(client, two_shops, staff):
    (shop_a, _), _ = two_shops
    client.login(email="root@plat.com", password="pass12345")
    csv = SimpleUploadedFile("p.csv", b"ITEM NAME,SKU-CODE,AVG.COST\nWidget,W1,1200\n")
    r = client.post("/platform/imports/upload/",
                    {"shop": shop_a.id, "import_type": "products", "file": csv})
    assert r.status_code == 302
    from imports.models import ImportJob
    job = ImportJob.objects.get()
    client.post(f"/platform/imports/{job.id}/map/",
                {"map_name": "0", "map_sku_code": "1", "map_avg_cost": "2"})
    job.refresh_from_db()
    assert job.status == ImportJob.Status.PREVIEW_READY
    client.post(f"/platform/imports/{job.id}/commit/", {})
    job.refresh_from_db()
    assert job.status == ImportJob.Status.COMMITTED and job.created_count == 1
