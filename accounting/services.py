"""
Transparent profit + financial position.

Rule (never violated): Gross Profit = Revenue − COGS; Net = Gross − Expenses.
COGS uses the cost SNAPSHOTTED on each sale item. Returns reduce revenue.
Services take ``shop`` explicitly and use ``all_objects`` with an explicit
shop filter so they are safe to call from Celery tasks and tests where no
thread-local tenant is set.
"""
from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.db.models.functions import Coalesce

from crm.models import Customer
from purchasing.models import Supplier
from sales.models import Sale, SaleItem, SaleReturn, SaleReturnItem

from .models import Expense, ExpenseCategory, LedgerEntry

ZERO = Decimal("0")
_DEC = DecimalField(max_digits=18, decimal_places=2)


def _sum(qs, expr):
    return qs.aggregate(v=Coalesce(Sum(expr, output_field=_DEC), ZERO, output_field=_DEC))["v"]


def profit_summary(shop, start=None, end=None):
    """Return a dict with revenue, COGS, gross/net profit, expenses for a range."""
    sales = Sale.all_objects.filter(shop_id=shop.id).exclude(status=Sale.Status.CANCELLED)
    items = SaleItem.all_objects.filter(shop_id=shop.id).exclude(
        sale__status=Sale.Status.CANCELLED
    )
    returns = SaleReturn.all_objects.filter(shop_id=shop.id)
    expenses = Expense.all_objects.filter(shop_id=shop.id)

    if start is not None:
        sales = sales.filter(sale_date__gte=start)
        items = items.filter(sale__sale_date__gte=start)
        returns = returns.filter(created_at__gte=start)
        expenses = expenses.filter(spent_on__gte=start)
    if end is not None:
        sales = sales.filter(sale_date__lte=end)
        items = items.filter(sale__sale_date__lte=end)
        returns = returns.filter(created_at__lte=end)
        expenses = expenses.filter(spent_on__lte=end)

    # Revenue is net of the invoice-level discount (item.subtotal only carries
    # line discounts) and excludes VAT (a pass-through liability, not income).
    revenue = _sum(items, "subtotal") - _sum(sales, "discount")
    cogs = _sum(
        items,
        ExpressionWrapper(F("quantity") * F("unit_cost"), output_field=_DEC),
    )
    returns_amount = _sum(returns, "total_refund")
    # Returned goods that were restocked weren't really sold: credit their
    # snapshotted COGS back, otherwise profit is understated (and the cost is
    # double-counted against the re-added inventory value).
    returned_cogs = _sum(
        SaleReturnItem.all_objects.filter(
            sale_return__in=returns.filter(restocked=True)
        ),
        ExpressionWrapper(F("quantity") * F("sale_item__unit_cost"), output_field=_DEC),
    )
    total_expenses = _sum(expenses, "amount")

    gross_profit = (revenue - returns_amount) - (cogs - returned_cogs)
    net_profit = gross_profit - total_expenses

    return {
        "revenue": revenue,
        "returns": returns_amount,
        "cogs": cogs - returned_cogs,
        "gross_profit": gross_profit,
        "expenses": total_expenses,
        "net_profit": net_profit,
        "sales_count": sales.count(),
    }


DEFAULT_EXPENSE_CATEGORIES = [
    "Rent", "Electricity", "Salary", "Transport", "Marketing", "Internet", "Other",
]


def seed_expense_categories(shop):
    """Create the preset expense categories for a shop (idempotent)."""
    for name in DEFAULT_EXPENSE_CATEGORIES:
        ExpenseCategory.all_objects.get_or_create(shop_id=shop.id, name=name)


def record_expense(*, shop, amount, spent_on, category=None, payment_method="", note="", created_by=None):
    """Create an expense AND post the cash outflow to the ledger."""
    amount = Decimal(amount)
    expense = Expense.all_objects.create(
        shop_id=shop.id, category=category, amount=amount, spent_on=spent_on,
        payment_method=payment_method, note=note, created_by=created_by,
    )
    LedgerEntry.all_objects.create(
        shop_id=shop.id, account=LedgerEntry.Account.CASH, amount=-amount,
        source_type="Expense", source_id=str(expense.id),
        description=note or (category.name if category else "Expense"),
    )
    return expense


def cash_flow(shop, start, end):
    """opening + inflow - outflow = closing, computed from the cash ledger."""
    ledger = LedgerEntry.all_objects.filter(shop_id=shop.id, account=LedgerEntry.Account.CASH)
    opening = _sum(ledger.filter(created_at__lt=start), "amount") if start else ZERO
    in_range = ledger
    if start:
        in_range = in_range.filter(created_at__gte=start)
    if end:
        in_range = in_range.filter(created_at__lte=end)
    inflow = _sum(in_range.filter(amount__gt=0), "amount")
    outflow = _sum(in_range.filter(amount__lt=0), "amount")  # negative
    closing = opening + inflow + outflow
    return {
        "opening_cash": opening, "inflow": inflow,
        "outflow": -outflow, "closing_cash": closing,
    }


def financial_position(shop):
    """Cash balance, receivables (customer dues), payables (supplier dues)."""
    cash = _sum(
        LedgerEntry.all_objects.filter(shop_id=shop.id, account=LedgerEntry.Account.CASH),
        "amount",
    )
    bank = _sum(
        LedgerEntry.all_objects.filter(shop_id=shop.id, account=LedgerEntry.Account.BANK),
        "amount",
    )
    receivables = _sum(Customer.all_objects.filter(shop_id=shop.id), "due_balance")
    payables = _sum(Supplier.all_objects.filter(shop_id=shop.id), "due_balance")
    return {
        "cash_balance": cash,
        "bank_balance": bank,
        "receivables": receivables,
        "payables": payables,
    }
