# imports — Super-Admin Bulk Data Import

Platform **super admin only** (`user.is_staff`). Lets a super admin bulk-import
data into **one chosen shop (tenant)** through a safe
`upload → map → validate → preview → confirm → commit` wizard, wrapped in a single
database transaction so a shop's live data can never be partially corrupted.

Mounted at `/platform/imports/`. Replaces the old flat `dues-csv` tool.

## Supported import types

| Type | Target | Match key | Write |
|------|--------|-----------|-------|
| `products` | `catalog.Product` | `sku` (per shop) | upsert name / cost / (opt) selling price / barcode |
| `supplier_dues` | `purchasing.Supplier` | phone → name | upsert party, overwrite `due_balance` |
| `customer_dues` | `crm.Customer` | phone → name | upsert party, overwrite `due_balance` |

### Design decisions (from Phase 0 discovery)
- **Dues are a cached `due_balance` field**, not a ledger table. Import overwrites
  it (running balance). Matched on normalized phone, then exact name, within the
  target shop only.
- **Product stock qty is NOT imported.** `current_stock` is derived from the
  append-only StockMovement ledger; opening stock is received via a PO. Only
  name / SKU / cost (+ optional selling price, barcode) are imported.
- Blank contact fields on an existing party are filled; **non-empty contact
  fields are never silently overwritten.**

## Expected file formats

CSV or XLSX. First row = headers (any header names — you map them manually, so
column *order* and *names* don't have to match). Money may include `৳`, spaces
and thousand separators — both Western `6,210.00` and Bengali lakh `2,96,300.00`
parse correctly. **Quote money fields that contain commas** in CSV
(`"৳1,200"`) so the comma isn't read as a column separator.

Sample headers:

```
# products.csv
ITEM NAME,SKU-CODE,AVG.COST,SELL,BARCODE
Widget,W1,"৳1,200",1500,800000001

# supplier_dues.csv
SUPPLIER NAME,PHONE,ADDRESS,EMAIL,DUE
Acme Traders,01700-000000,Dhaka,acme@x.com,"৳2,96,300.00"

# customer_dues.csv
NAME,PHONE,DUE
Ali Rahman,01810-111111,"৳6,210.00"
```

Unmapped columns (e.g. `ASSET VALUE`, `SN`) are ignored — never staged, never
written. Derived values are recomputed on our side.

## How to run an import

1. `/platform/imports/` → pick **target shop**, **import type**, upload the file.
2. **Map columns** — one dropdown per platform field, listing every detected
   column as `Header (Column N)`. Required fields (\*) must be mapped or you
   can't continue. Optionally **save the mapping as a template** / **load** one.
3. **Preview** — every row is staged (never written yet) with a badge
   (`will create` / `will update` / `warning` / `error`), inline messages, and
   totals. Filter to *only errors* / *only warnings*.
4. **Confirm & Commit** — disabled while there are error rows unless you tick
   *"skip error rows and import the rest"*. Commit runs one `transaction.atomic()`
   upsert; any failure rolls the whole job back and changes nothing.

## Rollback

A committed job shows **↩ Roll back** on its detail page:
- rows this job **created** are deleted;
- rows this job **updated** are restored from the `_previous` snapshot captured
  at commit time (stored on `ImportRow.cleaned_data["_previous"]`).
Rows still referenced by other data (PROTECT) are skipped and reported.

A row-level **result report (CSV)** is downloadable from the job detail page.

## Architecture

```
imports/
  models.py            ImportJob, ImportMapping, ImportRow (staging; UUID job id)
  services.py          detect_and_store_columns, stage_job, commit_job,
                       rollback_job, build_report_csv
  permissions.py       IsPlatformSuperAdmin (DRF) + @platform_superadmin_required
  views.py             HTMX wizard (upload/map/preview/commit/detail/report/rollback)
  importers/
    base.py            BaseImporter contract + normalization helpers
    fields.py          Field(name, label, required, kind)
    products.py        ProductImporter
    party_dues.py      shared supplier/customer logic
    supplier_dues.py   SupplierDuesImporter
    customer_dues.py   CustomerDuesImporter
    registry.py        IMPORTERS / get_importer
```

Every write sets the tenant FK explicitly and asserts
`obj.shop_id == job.shop_id` (`BaseImporter._assert_tenant`) so an import can
never leak rows into the wrong shop.

## Known limitation

Headerless files: the first row is always treated as the header row. For a file
with no header, map **by position** (`Column N`) — row 1 will be consumed as the
header. Add a "file has no header row" flag if truly headerless exports appear.

## Tests

`tests/test_imports.py` — normalization, validation, match/upsert + idempotency,
mapping (only-mapped-columns / positional / templates / required-gate),
permissions, tenant isolation, and transactional rollback on mid-commit failure.
