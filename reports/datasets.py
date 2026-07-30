"""
Report dataset builders. Each returns ``(title, columns, rows)``. Reuses the
analytics/accounting service functions — no aggregation logic duplicated here.
"""
from decimal import Decimal

from django.db.models import Count, DecimalField, Sum
from django.db.models.functions import Coalesce

from accounting.models import Expense
from accounting.services import profit_summary
from crm.models import Customer
from purchasing.models import PurchaseOrder, Supplier
from sales.models import Sale

ZERO = Decimal("0")
_DEC = DecimalField(max_digits=18, decimal_places=2)


def sales_report(shop, start=None, end=None):
    qs = Sale.all_objects.filter(shop_id=shop.id).select_related("customer")
    if start:
        qs = qs.filter(sale_date__gte=start)
    if end:
        qs = qs.filter(sale_date__lte=end)
    rows = [
        [s.invoice_no, s.sale_date.strftime("%Y-%m-%d"),
         s.customer.name if s.customer else "Walk-in",
         s.total, s.paid, s.due, s.get_status_display()]
        for s in qs
    ]
    return ("Sales Report", ["Invoice", "Date", "Customer", "Total", "Paid", "Due", "Status"], rows)


def purchase_report(shop, start=None, end=None):
    qs = PurchaseOrder.all_objects.filter(shop_id=shop.id).select_related("supplier")
    if start:
        qs = qs.filter(created_at__gte=start)
    if end:
        qs = qs.filter(created_at__lte=end)
    rows = [
        [p.po_number, p.supplier.name, p.get_status_display(), p.total, p.paid, p.due]
        for p in qs
    ]
    return ("Purchase Report", ["PO", "Supplier", "Status", "Total", "Paid", "Due"], rows)


def inventory_report(shop, **_):
    from catalog.models import Product
    rows = [
        [p.name, p.sku, p.current_stock, p.cost_price, p.current_stock * p.cost_price]
        for p in Product.all_objects.filter(shop_id=shop.id, is_active=True)
    ]
    return ("Inventory Report", ["Product", "SKU", "Stock", "Cost", "Stock Value"], rows)


def profit_report(shop, start=None, end=None):
    s = profit_summary(shop, start=start, end=end)
    rows = [[k.replace("_", " ").title(), v] for k, v in s.items()]
    return ("Profit Report", ["Metric", "Value"], rows)


def expense_report(shop, start=None, end=None):
    qs = Expense.all_objects.filter(shop_id=shop.id).select_related("category")
    if start:
        qs = qs.filter(spent_on__gte=start)
    if end:
        qs = qs.filter(spent_on__lte=end)
    rows = [[e.spent_on.strftime("%Y-%m-%d"), e.category.name if e.category else "-",
             e.amount, e.note] for e in qs]
    return ("Expense Report", ["Date", "Category", "Amount", "Note"], rows)


def customer_due_report(shop, **_):
    rows = [
        [c.name, c.phone, c.due_balance]
        for c in Customer.all_objects.filter(shop_id=shop.id, due_balance__gt=0)
    ]
    return ("Customer Due Report", ["Customer", "Phone", "Due"], rows)


def supplier_due_report(shop, **_):
    rows = [
        [s.name, s.phone, s.due_balance]
        for s in Supplier.all_objects.filter(shop_id=shop.id, due_balance__gt=0)
    ]
    return ("Supplier Due Report", ["Supplier", "Phone", "Payable"], rows)


def tax_report(shop, start=None, end=None):
    qs = Sale.all_objects.filter(shop_id=shop.id).exclude(status=Sale.Status.CANCELLED)
    if start:
        qs = qs.filter(sale_date__gte=start)
    if end:
        qs = qs.filter(sale_date__lte=end)
    rows = [[s.invoice_no, s.sale_date.strftime("%Y-%m-%d"), s.tax] for s in qs if s.tax]
    total = sum((r[2] for r in rows), ZERO)
    rows.append(["TOTAL", "", total])
    return ("Tax Report", ["Invoice", "Date", "Tax"], rows)


def employee_sales_report(shop, start=None, end=None):
    qs = Sale.all_objects.filter(shop_id=shop.id).exclude(status=Sale.Status.CANCELLED)
    if start:
        qs = qs.filter(sale_date__gte=start)
    if end:
        qs = qs.filter(sale_date__lte=end)
    grouped = (
        qs.values("created_by__email")
        .annotate(
            count=Count("id"),
            revenue=Coalesce(Sum("total", output_field=_DEC), ZERO, output_field=_DEC),
        )
        .order_by("-revenue")
    )
    rows = [[g["created_by__email"] or "Unknown", g["count"], g["revenue"]] for g in grouped]
    return ("Employee Sales Report", ["Employee", "Sales Count", "Revenue"], rows)


BUILDERS = {
    "sales": sales_report,
    "purchase": purchase_report,
    "inventory": inventory_report,
    "profit": profit_report,
    "expense": expense_report,
    "customer_due": customer_due_report,
    "supplier_due": supplier_due_report,
    "tax": tax_report,
    "employee_sales": employee_sales_report,
}
