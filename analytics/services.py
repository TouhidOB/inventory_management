"""
Analytics service layer — reusable aggregations powering the dashboard, the
reports export app (8.6), and future AI insights. Expensive aggregations are
cached per shop with a short TTL.

Field note: Phase 1 named the per-product threshold ``reorder_level`` and the
cost ``cost_price``; the spec calls these ``minimum_stock`` / ``purchase_price``.
Same fields, treated as synonyms here.
"""
from datetime import timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.core.cache import cache
from django.db.models import (
    Count,
    DecimalField,
    ExpressionWrapper,
    F,
    Min,
    Q,
    Sum,
)
from django.db.models.functions import Coalesce, TruncDate, TruncMonth, TruncWeek
from django.utils import timezone

from catalog.models import Product
from inventory.models import MovementType, StockMovement
from sales.models import Sale, SaleItem
from service.models import ServiceTicket

ZERO = Decimal("0")
_DEC = DecimalField(max_digits=18, decimal_places=2)


def _sum(qs, expr):
    return qs.aggregate(v=Coalesce(Sum(expr, output_field=_DEC), ZERO, output_field=_DEC))["v"]


def _sale_items(shop, start=None, end=None):
    qs = SaleItem.all_objects.filter(shop_id=shop.id).exclude(sale__status=Sale.Status.CANCELLED)
    if start is not None:
        qs = qs.filter(sale__sale_date__gte=start)
    if end is not None:
        qs = qs.filter(sale__sale_date__lte=end)
    return qs


def _sale_discount_total(shop, start=None, end=None):
    """Σ of invoice-level discounts over the same non-cancelled sales. Item
    subtotals only carry line discounts, so top-line revenue must subtract this."""
    qs = Sale.all_objects.filter(shop_id=shop.id).exclude(status=Sale.Status.CANCELLED)
    if start is not None:
        qs = qs.filter(sale_date__gte=start)
    if end is not None:
        qs = qs.filter(sale_date__lte=end)
    return _sum(qs, "discount")


# --- period helpers ----------------------------------------------------------

def period_bounds(kind, now=None):
    now = now or timezone.now()
    start_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if kind == "today":
        return start_day, now
    if kind == "week":
        return start_day - timedelta(days=now.weekday()), now
    if kind == "month":
        return start_day.replace(day=1), now
    if kind == "quarter":
        q_month = ((now.month - 1) // 3) * 3 + 1
        return start_day.replace(month=q_month, day=1), now
    if kind == "year":
        return start_day.replace(month=1, day=1), now
    raise ValueError(kind)


# ---------------------------------------------------------------------------
# Inventory analytics
# ---------------------------------------------------------------------------

def stock_value(shop):
    """Total capital tied in stock: sum(current_stock * cost_price)."""
    return _sum(
        Product.all_objects.filter(shop_id=shop.id, is_active=True),
        ExpressionWrapper(F("current_stock") * F("cost_price"), output_field=_DEC),
    )


def stock_by_category(shop):
    return list(
        Product.all_objects.filter(shop_id=shop.id, is_active=True)
        .values("category__name")
        .annotate(
            units=Coalesce(Sum("current_stock", output_field=_DEC), ZERO, output_field=_DEC),
            value=Coalesce(
                Sum(ExpressionWrapper(F("current_stock") * F("cost_price"), output_field=_DEC)),
                ZERO, output_field=_DEC,
            ),
        )
        .order_by("-value")
    )


def stock_by_brand(shop):
    return list(
        Product.all_objects.filter(shop_id=shop.id, is_active=True)
        .values("brand__name")
        .annotate(
            units=Coalesce(Sum("current_stock", output_field=_DEC), ZERO, output_field=_DEC),
            value=Coalesce(
                Sum(ExpressionWrapper(F("current_stock") * F("cost_price"), output_field=_DEC)),
                ZERO, output_field=_DEC,
            ),
        )
        .order_by("-value")
    )


def low_stock_list(shop):
    return list(
        Product.all_objects.filter(
            shop_id=shop.id, track_inventory=True, is_active=True,
            current_stock__gt=0, current_stock__lte=F("reorder_level"),
        ).values("id", "name", "sku", "current_stock", "reorder_level")
    )


def out_of_stock_list(shop):
    return list(
        Product.all_objects.filter(
            shop_id=shop.id, track_inventory=True, is_active=True, current_stock__lte=0,
        ).values("id", "name", "sku", "current_stock", "reorder_level")
    )


def dead_stock(shop, days=90):
    """
    Products with stock on hand but ZERO sale-out movements in the window.
    Returns the list plus total tied-up capital.
    """
    cutoff = timezone.now() - timedelta(days=days)
    sold_ids = (
        SaleItem.all_objects.filter(
            shop_id=shop.id, sale__sale_date__gte=cutoff,
        ).exclude(sale__status=Sale.Status.CANCELLED)
        .values_list("product_id", flat=True).distinct()
    )
    qs = Product.all_objects.filter(
        shop_id=shop.id, is_active=True, current_stock__gt=0
    ).exclude(id__in=list(sold_ids))

    products = list(
        qs.annotate(
            tied_capital=ExpressionWrapper(F("current_stock") * F("cost_price"), output_field=_DEC)
        ).values("id", "name", "current_stock", "cost_price", "tied_capital")
    )
    total = sum((p["tied_capital"] for p in products), ZERO)
    return {"window_days": days, "count": len(products),
            "tied_capital": total, "products": products}


# ---------------------------------------------------------------------------
# Sales analytics
# ---------------------------------------------------------------------------

def sales_rollups(shop):
    out = {}
    for kind in ("today", "week", "month", "quarter", "year"):
        start, end = period_bounds(kind)
        items = _sale_items(shop, start, end)
        revenue = _sum(items, "subtotal") - _sale_discount_total(shop, start, end)
        out[kind] = {
            "revenue": revenue,
            "cogs": _sum(items, ExpressionWrapper(F("quantity") * F("unit_cost"), output_field=_DEC)),
        }
        out[kind]["profit"] = out[kind]["revenue"] - out[kind]["cogs"]
    return out


def weekly_sales_trend(shop, weeks=12):
    start = timezone.now() - timedelta(weeks=weeks)
    return list(
        Sale.all_objects.filter(shop_id=shop.id, sale_date__gte=start)
        .exclude(status=Sale.Status.CANCELLED)
        .annotate(week=TruncWeek("sale_date"))
        .values("week")
        .annotate(revenue=Coalesce(Sum("total", output_field=_DEC), ZERO, output_field=_DEC))
        .order_by("week")
    )


def _growth(current, previous):
    if previous and previous != 0:
        return round(float((current - previous) / previous * 100), 2)
    return None if not current else 100.0


def mom_growth(shop):
    """Month-over-month revenue growth %."""
    m_start, now = period_bounds("month")
    prev_end = m_start - timedelta(seconds=1)
    prev_start = prev_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    this_rev = _sum(_sale_items(shop, m_start, now), "subtotal") - _sale_discount_total(shop, m_start, now)
    prev_rev = _sum(_sale_items(shop, prev_start, prev_end), "subtotal") - _sale_discount_total(shop, prev_start, prev_end)
    return {"current": this_rev, "previous": prev_rev, "growth_pct": _growth(this_rev, prev_rev)}


def _grouped_sales(shop, field, start, end):
    return {
        row[field]: row
        for row in _sale_items(shop, start, end)
        .values(field)
        .annotate(
            revenue=Coalesce(Sum("subtotal", output_field=_DEC), ZERO, output_field=_DEC),
            profit=Coalesce(
                Sum(ExpressionWrapper(F("subtotal") - F("quantity") * F("unit_cost"), output_field=_DEC)),
                ZERO, output_field=_DEC,
            ),
        )
    }


def category_sales(shop, period="month"):
    return _sales_by_dimension(shop, "product__category__name", period)


def brand_sales(shop, period="month"):
    return _sales_by_dimension(shop, "product__brand__name", period)


def _sales_by_dimension(shop, field, period):
    start, now = period_bounds(period)
    prev_end = start - timedelta(seconds=1)
    span = now - start
    prev_start = prev_end - span
    current = _grouped_sales(shop, field, start, now)
    previous = _grouped_sales(shop, field, prev_start, prev_end)
    rows = []
    for key, cur in current.items():
        prev_rev = previous.get(key, {}).get("revenue", ZERO)
        rows.append({
            "name": key or "Uncategorized",
            "revenue": cur["revenue"], "profit": cur["profit"],
            "growth_pct": _growth(cur["revenue"], prev_rev),
        })
    rows.sort(key=lambda r: r["revenue"], reverse=True)
    return rows


def top_products(shop, start=None, end=None, limit=10):
    return list(
        _sale_items(shop, start, end)
        .values("product_id", "product__name")
        .annotate(
            qty=Coalesce(Sum("quantity", output_field=_DEC), ZERO, output_field=_DEC),
            revenue=Coalesce(Sum("subtotal", output_field=_DEC), ZERO, output_field=_DEC),
            profit=Coalesce(
                Sum(ExpressionWrapper(F("subtotal") - F("quantity") * F("unit_cost"), output_field=_DEC)),
                ZERO, output_field=_DEC,
            ),
        )
        .order_by("-revenue")[:limit]
    )


def sales_by_category(shop, start=None, end=None):
    return list(
        _sale_items(shop, start, end)
        .values("product__category__name")
        .annotate(revenue=Coalesce(Sum("subtotal", output_field=_DEC), ZERO, output_field=_DEC))
        .order_by("-revenue")
    )


def product_performance(shop, product_id, period="month"):
    """Per-product units/revenue/profit + growth vs previous equal period."""
    start, now = period_bounds(period)
    span = now - start
    prev_end = start - timedelta(seconds=1)
    prev_start = prev_end - span

    def agg(s, e):
        items = _sale_items(shop, s, e).filter(product_id=product_id)
        rev = _sum(items, "subtotal")
        cogs = _sum(items, ExpressionWrapper(F("quantity") * F("unit_cost"), output_field=_DEC))
        return {
            "units": _sum(items, "quantity"),
            "revenue": rev, "profit": rev - cogs,
        }

    cur = agg(start, now)
    prev = agg(prev_start, prev_end)
    cur["revenue_growth_pct"] = _growth(cur["revenue"], prev["revenue"])
    cur["previous"] = prev
    return cur


def sales_trend(shop, days=30):
    start = timezone.now() - timedelta(days=days)
    return list(
        Sale.all_objects.filter(shop_id=shop.id, sale_date__gte=start)
        .exclude(status=Sale.Status.CANCELLED)
        .annotate(day=TruncDate("sale_date"))
        .values("day")
        .annotate(revenue=Coalesce(Sum("total", output_field=_DEC), ZERO, output_field=_DEC))
        .order_by("day")
    )


# ---------------------------------------------------------------------------
# Dashboard (cached)
# ---------------------------------------------------------------------------

def dashboard_summary(shop, days=30, use_cache=True):
    from django.conf import settings

    key = f"dashboard:{shop.id}:{days}"
    if use_cache:
        cached = cache.get(key)
        if cached is not None:
            return cached

    from accounting.services import financial_position, profit_summary

    now = timezone.now()
    start = now - timedelta(days=days)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    data = {
        "period_days": days,
        "today": profit_summary(shop, start=today_start, end=now),
        "period": profit_summary(shop, start=start, end=now),
        "position": financial_position(shop),
        "stock_value": stock_value(shop),
        "low_stock_count": len(low_stock_list(shop)),
        "out_of_stock_count": len(out_of_stock_list(shop)),
        "top_products": top_products(shop, start=start, end=now, limit=5),
        "sales_trend": sales_trend(shop, days=min(days, 30)),
        "mom_growth": mom_growth(shop),
    }
    if use_cache:
        cache.set(key, data, getattr(settings, "ANALYTICS_CACHE_TTL", 60))
    return data


def invalidate_dashboard_cache(shop_id):
    """Call after sales/stock changes to keep dashboards fresh."""
    for days in (7, 30, 90, 365):
        cache.delete(f"dashboard:{shop_id}:{days}")
    cache.delete(f"reports_charts:{shop_id}")


# ---------------------------------------------------------------------------
# Reports charts (repair-shop analytics)
#
# All functions return plain JSON-serializable primitives (str/int/float/list)
# so the reports view can hand them straight to Chart.js via json_script.
# ---------------------------------------------------------------------------

def _months_back(months):
    """First-of-month datetime for the window start, N months back inclusive."""
    now = timezone.now()
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return first - relativedelta(months=months - 1)


def _open_tickets(shop):
    """All non-cancelled tickets for the shop (unscoped manager, tenant-filtered)."""
    return ServiceTicket.all_objects.filter(shop_id=shop.id).exclude(
        status=ServiceTicket.Status.CANCELLED
    )


def revenue_parts_vs_labor(shop, months=6):
    """Monthly revenue split: parts (sale items) vs labor (service charges).

    Feeds the stacked area/line 'Revenue Trends Over Time' chart.
    """
    start = _months_back(months)
    parts_rows = (
        _sale_items(shop, start, timezone.now())
        .annotate(mo=TruncMonth("sale__sale_date"))
        .values("mo")
        .annotate(v=Coalesce(Sum("subtotal", output_field=_DEC), ZERO, output_field=_DEC))
    )
    parts = {(r["mo"].year, r["mo"].month): r["v"] for r in parts_rows}
    labor_rows = (
        _open_tickets(shop)
        .filter(received_at__gte=start)
        .annotate(mo=TruncMonth("received_at"))
        .values("mo")
        .annotate(v=Coalesce(Sum("service_charge", output_field=_DEC), ZERO, output_field=_DEC))
    )
    labor = {(r["mo"].year, r["mo"].month): r["v"] for r in labor_rows}

    labels, parts_series, labor_series = [], [], []
    for i in range(months):
        mo = start + relativedelta(months=i)
        key = (mo.year, mo.month)
        labels.append(mo.strftime("%b %Y"))
        parts_series.append(float(parts.get(key, ZERO)))
        labor_series.append(float(labor.get(key, ZERO)))
    return {"labels": labels, "parts": parts_series, "labor": labor_series}


def sales_mix(shop, period="month"):
    """Revenue proportion: hardware parts vs software/services vs repairs.

    Software/services are sale items whose category name mentions
    'software' or 'service'; everything else sold is treated as parts.
    Repairs are the period's service charges. Feeds the donut chart.
    """
    start, now = period_bounds(period)
    items = _sale_items(shop, start, now)
    soft_rev = _sum(
        items.filter(
            Q(product__category__name__icontains="software")
            | Q(product__category__name__icontains="service")
        ),
        "subtotal",
    )
    parts_rev = _sum(items, "subtotal") - soft_rev
    repairs = _sum(_open_tickets(shop).filter(received_at__gte=start, received_at__lte=now), "service_charge")
    return {
        "labels": ["Hardware parts", "Software/Services", "Repairs/Labor"],
        "values": [float(parts_rev), float(soft_rev), float(repairs)],
    }


# Bucket edges (upper bound inclusive) for turnaround-time histogram.
_TAT_BUCKETS = [(1, "0–1d"), (3, "2–3d"), (7, "4–7d"), (14, "8–14d"), (10**9, "15d+")]


def _percentile(sorted_vals, q):
    if not sorted_vals:
        return 0
    idx = min(int(round((len(sorted_vals) - 1) * q)), len(sorted_vals) - 1)
    return sorted_vals[idx]


def turnaround_time(shop):
    """Distribution of repair turnaround (received -> delivered) in days.

    Returns histogram buckets plus box-plot stats (min/q1/median/q3/max/avg).
    """
    rows = (
        ServiceTicket.all_objects.filter(shop_id=shop.id, actual_delivery__isnull=False)
        .values_list("received_at", "actual_delivery")
    )
    days = sorted(max((a - r).days, 0) for r, a in rows)
    counts = [0] * len(_TAT_BUCKETS)
    for d in days:
        for i, (upper, _label) in enumerate(_TAT_BUCKETS):
            if d <= upper:
                counts[i] += 1
                break
    stats = {
        "count": len(days),
        "min": days[0] if days else 0,
        "q1": _percentile(days, 0.25),
        "median": _percentile(days, 0.5),
        "q3": _percentile(days, 0.75),
        "max": days[-1] if days else 0,
        "avg": round(sum(days) / len(days), 1) if days else 0,
    }
    return {"labels": [label for _u, label in _TAT_BUCKETS], "values": counts, "stats": stats}


def repair_volume_by_device(shop, weeks=8):
    """Tickets received per week, stacked by device type. Feeds stacked bar."""
    start = timezone.now() - timedelta(weeks=weeks)
    rows = (
        _open_tickets(shop)
        .filter(received_at__gte=start)
        .annotate(wk=TruncWeek("received_at"))
        .values("wk", "device_type")
        .annotate(n=Count("id"))
        .order_by("wk")
    )
    weeks_seen = sorted({r["wk"].date() for r in rows})
    labels = [w.strftime("%b %d") for w in weeks_seen]
    week_index = {w: i for i, w in enumerate(weeks_seen)}
    device_labels = dict(ServiceTicket.DeviceType.choices)
    datasets = {code: [0] * len(weeks_seen) for code in device_labels}
    for r in rows:
        # Ignore any legacy/unknown device_type not in the current choices,
        # otherwise a stray value KeyErrors the whole dashboard.
        if r["device_type"] in datasets:
            datasets[r["device_type"]][week_index[r["wk"].date()]] = r["n"]
    # Drop device types with no tickets in the window to keep the legend clean.
    series = [
        {"label": device_labels[code], "data": data}
        for code, data in datasets.items()
        if any(data)
    ]
    return {"labels": labels, "datasets": series}


def issue_pareto(shop):
    """Issue-type frequency, descending, with cumulative %. Feeds Pareto chart."""
    rows = (
        _open_tickets(shop)
        .values("issue_type")
        .annotate(n=Count("id"))
        .order_by("-n")
    )
    issue_labels = dict(ServiceTicket.IssueType.choices)
    labels = [issue_labels.get(r["issue_type"], r["issue_type"]) for r in rows]
    counts = [r["n"] for r in rows]
    total = sum(counts) or 1
    cumulative, running = [], 0
    for n in counts:
        running += n
        cumulative.append(round(running / total * 100, 1))
    return {"labels": labels, "counts": counts, "cumulative": cumulative}


def stock_turnover(shop):
    """Scatter points: x=days in inventory, y=profit margin %, per product.

    Highlights dead stock (old + low margin) vs winners (fresh + high margin).
    Days in inventory = age of the earliest purchase-in movement.
    """
    now = timezone.now()
    first_in = dict(
        StockMovement.all_objects.filter(
            shop_id=shop.id,
            movement_type__in=[MovementType.PURCHASE_IN, MovementType.OPENING],
        )
        .values("product_id")
        .annotate(first=Min("created_at"))
        .values_list("product_id", "first")
    )
    points = []
    for p in Product.all_objects.filter(shop_id=shop.id, is_active=True, current_stock__gt=0):
        first = first_in.get(p.id)
        if first is None:
            continue
        selling = Decimal(p.selling_price or 0)
        if selling <= 0:
            continue
        margin = float((selling - Decimal(p.cost_price or 0)) / selling * 100)
        points.append({
            "x": (now - first).days,
            "y": round(margin, 1),
            "label": p.name,
        })
    return {"points": points}


def reorder_status(shop, limit=12):
    """Current stock vs reorder threshold for the most-at-risk tracked items.

    Feeds the bullet/gauge chart. Ordered by how far below threshold (most
    urgent first).
    """
    rows = [
        {
            "name": p.name,
            "current": float(p.current_stock or 0),
            "threshold": float(p.reorder_level or 0),
        }
        for p in Product.all_objects.filter(
            shop_id=shop.id, track_inventory=True, is_active=True, reorder_level__gt=0
        )
    ]
    rows.sort(key=lambda r: r["current"] - r["threshold"])
    return rows[:limit]


def customer_acquisition(shop, months=6):
    """Monthly first-time vs returning customers. Feeds stacked column chart."""
    start = _months_back(months)
    base = Sale.all_objects.filter(shop_id=shop.id, customer__isnull=False).exclude(
        status=Sale.Status.CANCELLED
    )
    first_purchase = {
        r["customer_id"]: r["f"]
        for r in base.values("customer_id").annotate(f=Min("sale_date"))
    }
    # Distinct (customer, month) pairs active within the window.
    active = (
        base.filter(sale_date__gte=start)
        .annotate(mo=TruncMonth("sale_date"))
        .values("customer_id", "mo")
        .distinct()
    )
    labels, new_series, ret_series = [], [], []
    buckets = {}
    for i in range(months):
        mo = start + relativedelta(months=i)
        buckets[(mo.year, mo.month)] = [0, 0]  # [new, returning]
        labels.append(mo.strftime("%b %Y"))
    for row in active:
        key = (row["mo"].year, row["mo"].month)
        if key not in buckets:
            continue
        first = first_purchase.get(row["customer_id"])
        is_new = first and (first.year, first.month) == key
        buckets[key][0 if is_new else 1] += 1
    for i in range(months):
        mo = start + relativedelta(months=i)
        n, r = buckets[(mo.year, mo.month)]
        new_series.append(n)
        ret_series.append(r)
    return {"labels": labels, "new": new_series, "returning": ret_series}


def reports_charts(shop, use_cache=True):
    """Bundle every reports-page chart dataset into one JSON-ready dict.

    ~10 independent aggregations (≈13-20 queries). Cached on the same short TTL
    as the dashboard summary — the reports page and every dashboard load hit
    this, so caching cuts their query count substantially on warm loads."""
    from django.conf import settings

    key = f"reports_charts:{shop.id}"
    if use_cache:
        cached = cache.get(key)
        if cached is not None:
            return cached

    now = timezone.now()
    start = now - timedelta(days=90)
    tops = top_products(shop, start=start, end=now, limit=10)
    data = {
        "revenue_trend": revenue_parts_vs_labor(shop, months=6),
        "sales_mix": sales_mix(shop, period="month"),
        "top_parts": {
            "labels": [r["product__name"] for r in tops],
            "values": [float(r["revenue"]) for r in tops],
        },
        "tat": turnaround_time(shop),
        "device_volume": repair_volume_by_device(shop, weeks=8),
        "issue_pareto": issue_pareto(shop),
        "stock_turnover": stock_turnover(shop),
        "reorder": reorder_status(shop),
        "acquisition": customer_acquisition(shop, months=6),
    }
    if use_cache:
        cache.set(key, data, getattr(settings, "ANALYTICS_CACHE_TTL", 60))
    return data
