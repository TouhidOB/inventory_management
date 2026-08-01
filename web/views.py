"""
Server-rendered frontend (Django templates + Alpine/HTMX).

Security: every view is guarded by ``shop_member_required`` (auth + shop
membership) and, where relevant, ``perm_required``. All data access goes through
the tenant-scoped managers, so a shop only ever sees its own rows; detail views
use ``get_object_or_404`` against the scoped manager, so guessing another shop's
object id returns 404.
"""
import json
import uuid
from datetime import datetime, timedelta
from datetime import time as dt_time
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.http import JsonResponse
from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.models import Permission, Role, RoleType, User
from accounting.models import Expense, ExpenseCategory
from accounting.services import cash_flow, financial_position, profit_summary, record_expense
from analytics.services import (
    dashboard_summary,
    reports_charts,
    sales_by_category,
    sales_trend,
    top_products,
)
from catalog.models import Brand, Category, Product
from crm.models import Customer
from inventory.models import MovementType, StockMovement
from inventory.services import apply_movement, restock
from notifications.models import Notification
from platform_admin.models import TutorialVideo
from purchasing.models import PurchaseOrder, Supplier
from purchasing.services import (
    add_purchase_payment,
    create_purchase_order,
    pay_supplier,
    receive_purchase_order,
)
from sales.models import Sale, SaleItem
from sales.services import add_payment, collect_customer_due, create_sale, edit_sale
from service.models import ServiceTicket, Warranty
from service.services import (
    add_ticket_part, add_ticket_payment, change_ticket_status, create_ticket,
    lookup_warranties,
)
from tenants.models import SubscriptionPlan

from .guards import perm_required, shop_member_required


def _parse_date(value):
    """Parse YYYY-MM-DD, returning None for empty, malformed, or impossible
    dates (e.g. ``2026-13-45``) instead of letting a bad string reach a
    DateField and raise ValidationError → 500."""
    from django.utils.dateparse import parse_date
    if not value:
        return None
    try:
        return parse_date(value)
    except ValueError:
        return None


def _json_body(request):
    """Parse a JSON request body, returning None (caller should 400) when the
    body is missing, malformed, or not a JSON object — so a bad payload can't
    raise JSONDecodeError / AttributeError and 500 the endpoint."""
    try:
        data = json.loads(request.body or "{}")
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _period_range(period):
    """Return (start, end, label) for daily/weekly/monthly/yearly filters."""
    now = timezone.now()
    if period == "weekly":
        start = now - timedelta(days=now.weekday())
    elif period == "monthly":
        start = now.replace(day=1)
    elif period == "yearly":
        start = now.replace(month=1, day=1)
    else:  # daily
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        period = "daily"
    return start.replace(hour=0, minute=0, second=0, microsecond=0), now, period


# Field-size guards. The DecimalFields cap at max_digits=14 (12 integer digits)
# for totals and max_digits=12 (10 integer digits) for money/price fields. A raw
# string like "1e40", "NaN" or "Infinity" parses to a valid Decimal but blows up
# at the DB layer ("value must be a decimal number" / overflow). Clamp to a finite,
# in-range magnitude so no user input can 500 the save.
_DEC_LIMIT = Decimal("999999999999.99")   # 12 integer digits — safe for 14-digit fields
_MONEY_LIMIT = Decimal("9999999999.99")   # 10 integer digits — safe for 12-digit money fields


def _dec(value, default="0"):
    try:
        d = Decimal(str(value or default))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)
    if not d.is_finite():          # reject NaN / Infinity
        return Decimal(default)
    if d > _DEC_LIMIT:
        return _DEC_LIMIT
    if d < -_DEC_LIMIT:
        return -_DEC_LIMIT
    return d


def _dec_nn(value, default="0"):
    """Non-negative, in-range decimal for money/price fields.

    Floors negatives to 0 (the number inputs have no ``min``, so a browser user
    can still type ``-100`` — negative prices corrupt COGS and the cash ledger)
    and caps at the 12-digit money-field limit so huge values can't overflow the
    column and 500 the save."""
    d = _dec(value, default)
    if d < 0:
        return Decimal("0")
    if d > _MONEY_LIMIT:
        return _MONEY_LIMIT
    return d


def _clip(value, maxlen):
    """Trim free-text to a CharField's max_length. SQLite silently stores
    over-length strings but Postgres/MySQL raise DataError → 500, so clip at the
    view boundary to keep behaviour consistent across backends."""
    return (value or "").strip()[:maxlen]


def _clamp_pct(value):
    """Discount percent clamped to the 0..100 range."""
    d = _dec(value)
    if d < 0:
        return Decimal("0")
    if d > 100:
        return Decimal("100")
    return d


def _barcode_taken(shop, code, exclude_product_pk=None):
    """True if ``code`` is already used within the shop — either as a product
    barcode or a per-unit (ProductUnit) barcode. Barcodes are unique per shop."""
    from catalog.models import ProductUnit
    code = (code or "").strip()
    if not code:
        return False
    prod_q = Product.all_objects.filter(shop_id=shop.id, barcode=code)
    if exclude_product_pk is not None:
        prod_q = prod_q.exclude(pk=exclude_product_pk)
    if prod_q.exists():
        return True
    return ProductUnit.all_objects.filter(shop_id=shop.id, barcode=code).exists()


def _valid_email(value):
    """True if ``value`` is a syntactically valid email address."""
    from django.core.validators import validate_email
    from django.core.exceptions import ValidationError
    try:
        validate_email(value)
        return True
    except ValidationError:
        return False


def _int_or_none(value):
    """Coerce a POST/JSON id to int, or None if missing/non-numeric. Filtering a
    numeric pk column with a non-numeric string raises ValueError → 500, so a
    tampered dropdown/id would otherwise crash instead of 404/ignore."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _logo_error(f):
    """Validate an uploaded logo. The settings view saves via raw ``model.save()``
    (no form/full_clean), so ImageField never runs its own checks — a PDF or a
    100 MB file would otherwise be stored as-is. Return an error string, else None."""
    MAX = 5 * 1024 * 1024  # 5 MB
    if f.size > MAX:
        return "Logo must be under 5 MB."
    try:
        from PIL import Image
        Image.open(f).verify()   # raises for non-images / corrupt data
    except Exception:
        return "Logo must be a valid image file (PNG or JPG)."
    finally:
        f.seek(0)                # rewind so the file can still be saved
    return None


def _paginate(request, qs, per_page=15):
    """Return a Page for ``qs`` using the ?page= query param."""
    from django.core.paginator import Paginator
    return Paginator(qs, per_page).get_page(request.GET.get("page"))


def _unique_role_slug(shop, name):
    """Slugify a custom role name into a per-shop-unique role_type identifier."""
    import re

    base = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")[:26] or "role"
    slug = base
    n = 1
    existing = set(
        Role.objects.filter(shop_id=shop.id).values_list("role_type", flat=True)
    )
    while slug in existing:
        n += 1
        slug = f"{base}_{n}"[:30]
    return slug


# --- Public marketing pages (no auth) ---------------------------------------

def _plans_for_public():
    return SubscriptionPlan.objects.filter(is_active=True).order_by("price_monthly")


def public_home(request):
    if request.user.is_authenticated and request.user.shop_id:
        return redirect("web:dashboard")
    return render(request, "web/public/home.html", {"plans": _plans_for_public()})


def public_features(request):
    return render(request, "web/public/features.html")


def public_pricing(request):
    return render(request, "web/public/pricing.html", {"plans": _plans_for_public()})


@require_http_methods(["GET", "POST"])
def public_contact(request):
    if request.method == "POST":
        if request.POST.get("website"):  # honeypot: bots fill hidden field
            return redirect("web:contact")
        name = request.POST.get("name", "").strip()
        email = request.POST.get("email", "").strip()
        message = request.POST.get("message", "").strip()
        if not (name and email and message):
            messages.error(request, "Name, email and message are required.")
        else:
            from platform_admin.models import ContactMessage
            ContactMessage.objects.create(
                name=name, email=email,
                phone=request.POST.get("phone", ""),
                subject=request.POST.get("subject", ""),
                message=message,
            )
            messages.success(request, "Thanks! Your message has been sent — we'll get back to you.")
            return redirect("web:contact")
    return render(request, "web/public/contact.html")


@require_http_methods(["GET", "POST"])
def public_signup(request):
    """
    Request access. Accounts are NOT self-service — a visitor submits a request
    and the platform team provisions the shop from the Super Admin dashboard.
    The request is stored as a ContactMessage lead.
    """
    if request.method == "POST":
        if request.POST.get("website"):  # honeypot
            return redirect("web:signup")
        shop_name = request.POST.get("shop_name", "").strip()
        name = request.POST.get("owner_name", "").strip()
        email = request.POST.get("owner_email", "").strip()
        if not (shop_name and name and email):
            messages.error(request, "Shop name, your name and email are required.")
        else:
            from platform_admin.models import ContactMessage
            btype = request.POST.get("business_type", "general")
            note = request.POST.get("note", "").strip()
            ContactMessage.objects.create(
                name=name, email=email, phone=request.POST.get("phone", ""),
                subject=f"Account request — {shop_name} ({btype})",
                message=(f"Shop: {shop_name}\nBusiness type: {btype}\n"
                         f"Requested by: {name} <{email}>\n\n{note}"),
            )
            messages.success(
                request,
                "Thanks! Your access request has been received. Our team will "
                "review it and set up your account shortly.",
            )
            return redirect("web:signup")
    from tenants.models import Shop
    return render(request, "web/public/signup.html", {"business_types": Shop.BusinessType.choices})


# --- Auth -------------------------------------------------------------------

LOGIN_MAX_ATTEMPTS = 8            # per-(email, IP) failures before a temporary lock
LOGIN_LOCK_SECONDS = 15 * 60      # lock window
LOGIN_ACCT_MAX_ATTEMPTS = 30      # per-account cap (any IP) — blunts distributed
LOGIN_ACCT_LOCK_SECONDS = 30 * 60 # credential-stuffing while keeping DoS-lockout unlikely


def _client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR", "")) or "?"


@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        if request.user.shop_id:
            return redirect("web:dashboard")
        if request.user.is_staff:
            return redirect("platform:dashboard")
    if request.method == "POST":
        from django.core.cache import cache
        email = request.POST.get("email", "").strip().lower()
        password = request.POST.get("password", "")
        # Brute-force guard: throttle repeated failures per (email, IP) AND per
        # account across all IPs (blunts distributed credential stuffing). Cache
        # is Redis in prod (shared across workers), LocMem in dev.
        lock_key = f"loginfail:{email}:{_client_ip(request)}"
        acct_key = f"loginfail_acct:{email}"
        if cache.get(lock_key, 0) >= LOGIN_MAX_ATTEMPTS or cache.get(acct_key, 0) >= LOGIN_ACCT_MAX_ATTEMPTS:
            messages.error(request, "Too many failed attempts. Try again later.")
            return render(request, "web/login.html")
        user = authenticate(request, username=email, password=password)
        if user is None:
            cache.set(lock_key, cache.get(lock_key, 0) + 1, LOGIN_LOCK_SECONDS)
            cache.set(acct_key, cache.get(acct_key, 0) + 1, LOGIN_ACCT_LOCK_SECONDS)
            messages.error(request, "Invalid email or password.")
        elif user.shop_id is None:
            if user.is_staff:  # platform super admin
                cache.delete(lock_key); cache.delete(acct_key)
                login(request, user)
                return redirect("platform:dashboard")
            messages.error(request, "This account has no shop.")
        else:
            cache.delete(lock_key); cache.delete(acct_key)  # reset on success
            login(request, user)
            return redirect("web:dashboard")
    return render(request, "web/login.html")


def logout_view(request):
    logout(request)
    return redirect("web:login")


# --- Dashboard --------------------------------------------------------------

@shop_member_required
def dashboard(request):
    shop = request.user.shop
    can_reports = request.user.has_perm_code("view_reports")
    summary = dashboard_summary(shop, days=30) if can_reports else None
    ctx = {
        "active": "dashboard",
        "summary": summary,
        "charts": reports_charts(shop) if can_reports else None,
        "can_reports": can_reports,
        "can_profit": request.user.has_perm_code("view_profit"),
        "low_stock": Product.objects.filter(track_inventory=True, current_stock__lte=0)[:10],
        # Platform-wide help videos (super-admin managed), shown as a strip on the
        # dashboard; the full list lives on the Tutorials page.
        "tutorials": TutorialVideo.objects.filter(is_active=True)[:6],
    }
    return render(request, "web/dashboard.html", ctx)


@shop_member_required
def tutorials(request):
    """Video tutorials (managed by the platform super-admin), in sequence."""
    return render(request, "web/tutorials.html", {
        "active": "tutorials",
        "videos": TutorialVideo.objects.filter(is_active=True),
    })


@shop_member_required
def dashboard_chart_data(request):
    """JSON feeding the dashboard Chart.js charts (item 4)."""
    shop = request.user.shop
    trend = sales_trend(shop, days=30)
    tops = top_products(shop, limit=6)
    return JsonResponse({
        "trend": {
            "labels": [str(r["day"]) for r in trend],
            "values": [float(r["revenue"]) for r in trend],
        },
        "top_products": {
            "labels": [r["product__name"] for r in tops],
            "values": [float(r["revenue"]) for r in tops],
        },
    })


@shop_member_required
def barcode_resolve(request):
    """Resolve a scanned code to a product — matches the product barcode/SKU OR a
    per-unit barcode (#6). Used as a POS fallback when a unit barcode is scanned."""
    from catalog.models import ProductUnit
    code = (request.GET.get("code") or "").strip()
    if not code:
        return JsonResponse({"error": "No code."}, status=400)
    product = Product.objects.filter(barcode=code).first() or Product.objects.filter(sku=code).first()
    # Price/cost come from the scanned UNIT when a per-unit barcode is scanned, so
    # an older batch sells at ITS snapshotted price — not the product's latest.
    price = cost = warranty = None
    if product is None:
        unit = (
            ProductUnit.objects.filter(barcode=code, status=ProductUnit.Status.IN_STOCK)
            .select_related("product").first()
            or ProductUnit.objects.filter(barcode=code).select_related("product").first()
        )
        if unit is not None:
            product = unit.product
            price = unit.effective_selling_price
            cost = unit.effective_cost_price
            warranty = unit.effective_warranty_months
    if product is None:
        return JsonResponse({"error": "Not found."}, status=404)
    if price is None:  # product-level barcode/SKU scan
        price = product.selling_price or 0
        cost = product.cost_price or 0
        warranty = product.warranty_months or 0
    return JsonResponse({
        "id": product.id, "name": product.name,
        "price": str(price or 0), "cost": str(cost or 0),
        "warranty": warranty or 0, "stock": str(product.current_stock or 0),
        "barcode": product.barcode or "",
    })


@shop_member_required
def universal_search(request):
    """Item 8 — global search across invoices, products, customers, suppliers.
    Returns results grouped by data type for the header search dropdown."""
    from django.db.models import Q
    q = request.GET.get("q", "").strip()
    out = {"invoices": [], "products": [], "customers": [], "suppliers": []}
    if not q:
        return JsonResponse(out)

    for sale in (Sale.objects.filter(Q(invoice_no__icontains=q) | Q(customer__name__icontains=q))
                 .select_related("customer").order_by("-sale_date")[:8]):
        out["invoices"].append({"label": sale.invoice_no,
                                "sub": (sale.customer.name if sale.customer_id else sale.customer_name) + f" · {sale.total}",
                                "url": reverse("web:sale_detail", args=[sale.id])})
    # Products match by name/barcode/sku OR a scanned per-unit barcode (#6).
    prods = (Product.objects.filter(
        Q(name__icontains=q) | Q(barcode__icontains=q) | Q(sku__icontains=q) | Q(units__barcode__icontains=q)
    ).distinct()[:8])
    for p in prods:
        out["products"].append({"label": p.name, "sub": (p.barcode or p.sku or "") + f" · stock {p.current_stock}",
                                "url": reverse("web:product_profile", args=[p.id])})
    for c in Customer.objects.filter(Q(name__icontains=q) | Q(phone__icontains=q))[:8]:
        out["customers"].append({"label": c.name, "sub": c.phone,
                                 "url": reverse("web:customers") + f"?q={c.phone or c.name}"})
    for s in Supplier.objects.filter(Q(name__icontains=q) | Q(phone__icontains=q))[:8]:
        out["suppliers"].append({"label": s.name, "sub": s.phone,
                                 "url": reverse("web:suppliers")})
    return JsonResponse(out)


# --- Products ---------------------------------------------------------------

@shop_member_required
def products(request):
    if request.method == "POST":
        if not request.user.has_perm_code("manage_products"):
            messages.error(request, "Not allowed.")
            return redirect("web:products")

        action = request.POST.get("action", "add_product")
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

        if action == "add_category":
            name = request.POST.get("category_name", "").strip()
            obj = None
            if name:
                obj, created = Category.objects.get_or_create(
                    shop=request.user.shop, name=name, parent=None
                )
                messages.success(request, "Category added." if created else "Category already exists.")
            if is_ajax:
                return JsonResponse({"id": obj.id, "name": obj.name} if obj else {"error": "Name required."},
                                    status=200 if obj else 400)
            return redirect("web:products")

        if action == "add_subcategory":
            name = request.POST.get("subcategory_name", "").strip()
            parent = Category.objects.filter(pk=_int_or_none(request.POST.get("parent"))).first()
            obj = None
            if name and parent is not None:
                obj, created = Category.objects.get_or_create(
                    shop=request.user.shop, name=name, parent=parent
                )
                messages.success(request, "Subcategory added." if created else "Subcategory already exists.")
            else:
                messages.error(request, "Pick a parent category and a name.")
            if is_ajax:
                return JsonResponse({"id": obj.id, "name": obj.name, "parent_id": obj.parent_id} if obj
                                    else {"error": "Pick a parent and a name."}, status=200 if obj else 400)
            return redirect("web:products")

        if action == "add_brand":
            name = request.POST.get("brand_name", "").strip()
            obj = None
            if name:
                obj, created = Brand.objects.get_or_create(shop=request.user.shop, name=name)
                messages.success(request, "Brand added." if created else "Brand already exists.")
            if is_ajax:
                return JsonResponse({"id": obj.id, "name": obj.name} if obj else {"error": "Name required."},
                                    status=200 if obj else 400)
            return redirect("web:products")

        if action == "add_supplier":
            name = request.POST.get("supplier_name", "").strip()
            email = request.POST.get("supplier_email", "").strip()
            obj = None
            err = None
            if not name:
                err = "Name required."
            elif email and not _valid_email(email):
                err = "Enter a valid email address."
            else:
                obj = Supplier.objects.create(
                    shop=request.user.shop, name=_clip(name, 150),
                    company_name=_clip(request.POST.get("supplier_company", ""), 180),
                    phone=_clip(request.POST.get("supplier_phone", ""), 30), email=_clip(email, 254),
                )
                messages.success(request, "Supplier added.")
            if err and not is_ajax:
                messages.error(request, err)
            if is_ajax:
                return JsonResponse({"id": obj.id, "name": obj.name} if obj else {"error": err or "Name required."},
                                    status=200 if obj else 400)
            return redirect("web:products")

        # default: add product — one unified form.
        shop = request.user.shop
        # Category: prefer the subcategory, else the parent category.
        category_raw = request.POST.get("subcategory") or request.POST.get("category") or None
        category_id = None if category_raw in ("", "__new__", None) else _int_or_none(category_raw)
        brand_raw = request.POST.get("brand") or None
        brand_id = None if brand_raw in ("", "__new__", None) else _int_or_none(brand_raw)
        supplier_raw = request.POST.get("supplier") or None
        supplier_id = None if supplier_raw in ("", "__new__", None) else _int_or_none(supplier_raw)

        # Backward-compat inline "add new" for category/brand (older form posts).
        if request.POST.get("category") == "__new__":
            nc = request.POST.get("category_new", "").strip()
            if nc:
                cat, _ = Category.objects.get_or_create(shop=shop, name=nc, parent=None)
                category_id = cat.id
        if request.POST.get("brand") == "__new__":
            nb = request.POST.get("brand_new", "").strip()
            if nb:
                br, _ = Brand.objects.get_or_create(shop=shop, name=nb)
                brand_id = br.id

        # Drop dropdown ids that don't resolve to one of THIS shop's rows: a
        # stale/tampered/other-shop id would otherwise hit the FK constraint
        # (IntegrityError → 500) or link across tenants. Managers are scoped.
        if category_id and not Category.objects.filter(pk=category_id).exists():
            category_id = None
        if brand_id and not Brand.objects.filter(pk=brand_id).exists():
            brand_id = None
        if supplier_id and not Supplier.objects.filter(pk=supplier_id).exists():
            supplier_id = None

        # Auto-generate a unique SKU when left blank.
        sku = request.POST.get("sku", "").strip()
        if not sku:
            n = Product.all_objects.filter(shop_id=shop.id).count() + 1
            sku = f"SKU-{n:05d}"
            while Product.all_objects.filter(shop_id=shop.id, sku=sku).exists():
                n += 1
                sku = f"SKU-{n:05d}"
        elif Product.all_objects.filter(shop_id=shop.id, sku=sku).exists():
            # A user-supplied SKU that already exists would hit the (shop, sku)
            # unique constraint and raise IntegrityError → 500. Reject cleanly.
            msg = f"SKU “{sku}” is already in use."
            if is_ajax:
                return JsonResponse({"error": msg}, status=400)
            messages.error(request, msg)
            return redirect(request.POST.get("next") or "web:products")

        # Barcodes are unique per shop (against products + per-unit barcodes).
        barcode = request.POST.get("barcode", "").strip()
        if barcode and _barcode_taken(shop, barcode):
            msg = f"Barcode “{barcode}” is already in use."
            if is_ajax:
                return JsonResponse({"error": msg}, status=400)
            messages.error(request, msg)
            return redirect(request.POST.get("next") or "web:products")

        name = request.POST.get("name", "").strip()
        if not name:
            msg = "Product name is required."
            if is_ajax:
                return JsonResponse({"error": msg}, status=400)
            messages.error(request, msg)
            return redirect(request.POST.get("next") or "web:products")

        warranty_months = int(request.POST.get("warranty_months") or 0)
        product = Product.objects.create(
            shop=shop,
            name=_clip(name, 200),
            sku=_clip(sku, 60),
            barcode=_clip(barcode, 60),
            cost_price=_dec_nn(request.POST.get("cost_price")),
            selling_price=_dec_nn(request.POST.get("selling_price")),
            reorder_level=_dec_nn(request.POST.get("reorder_level")),
            category_id=category_id,
            brand_id=brand_id,
            supplier_id=supplier_id,
            warranty_months=warranty_months,
        )
        # Warranty duration also lands in the warranty table (item 10 / spec 10).
        if warranty_months > 0:
            from service.models import Warranty
            Warranty.objects.create(shop=shop, product=product, period_months=warranty_months)
        messages.success(request, f"Product added (SKU {sku}).")
        if is_ajax:
            return JsonResponse({
                "id": product.id, "name": product.name, "sku": product.sku,
                "barcode": product.barcode, "stock": str(product.current_stock or 0),
                "cost": str(product.cost_price or 0), "selling": str(product.selling_price or 0),
            })
        return redirect(request.POST.get("next") or "web:products")

    q = request.GET.get("q", "")
    qs = Product.objects.select_related("category", "brand").order_by("name")
    if q:
        from django.db.models import Q
        qs = qs.filter(Q(name__icontains=q) | Q(sku__icontains=q) | Q(barcode__icontains=q))

    # Hierarchical category options: "Parent › Child" so a product can be
    # assigned to either a top-level category or a subcategory.
    cats = Category.objects.select_related("parent").order_by("parent__name", "name")
    category_options = [
        {"id": c.id, "label": (f"{c.parent.name} › {c.name}" if c.parent_id else c.name)}
        for c in cats
    ]
    parent_categories = Category.objects.filter(parent__isnull=True).order_by("name")
    subcategories = list(
        Category.objects.filter(parent__isnull=False).values("id", "name", "parent_id")
    )
    page = _paginate(request, qs)
    return render(request, "web/products.html", {
        "active": "products", "products": page, "page_obj": page, "q": q,
        "category_options": category_options,
        "parent_categories": parent_categories,
        "subcategories": subcategories,
        "brands": Brand.objects.all(),
        "suppliers": Supplier.objects.order_by("name"),
    })


@shop_member_required
def product_profile(request, pk):
    """Product profile (#7): overview + per-unit barcode list showing which
    scanned items are still unsold (in stock) vs sold."""
    from catalog.models import ProductUnit
    from django.db.models import Sum
    product = get_object_or_404(Product.objects.select_related("category", "brand", "supplier"), pk=pk)
    units = ProductUnit.objects.filter(product=product).select_related("sale").order_by("status", "-created_at")
    in_stock = units.filter(status=ProductUnit.Status.IN_STOCK).count()
    sold = units.filter(status=ProductUnit.Status.SOLD).count()
    page = _paginate(request, units, per_page=30)
    sold_qty = (SaleItem.objects.filter(product=product)
                .exclude(sale__status=Sale.Status.CANCELLED)
                .aggregate(q=Sum("quantity"))["q"] or 0)

    # --- Batch breakdown -------------------------------------------------
    # A "batch" = the in-stock units that share a (purchase price, selling
    # price). Each keeps its OWN cost, selling price, and profit margin. The
    # product-level figures are the qty-weighted AVERAGE across all batches.
    from django.db.models import Count
    from decimal import Decimal as _D
    p_cost = _D(product.cost_price or 0)
    p_sell = _D(product.selling_price or 0)
    rows = (
        ProductUnit.objects.filter(product=product, status=ProductUnit.Status.IN_STOCK)
        .values("cost_price", "selling_price")
        .annotate(qty=Count("id"))
        .order_by("-selling_price")
    )
    batches = []
    for r in rows:
        cost = _D(r["cost_price"]) if r["cost_price"] is not None else p_cost
        sell = _D(r["selling_price"]) if r["selling_price"] is not None else p_sell
        qty = r["qty"]
        profit = sell - cost
        margin = (profit / sell * 100) if sell else _D(0)
        batches.append({
            "cost": cost, "sell": sell, "qty": qty,
            "profit_unit": profit, "margin": margin,
            "stock_value": cost * qty, "profit_potential": profit * qty,
        })
    # Products with non-serialized stock (no per-unit barcodes) → one synthetic
    # batch from the product's own prices + current stock, so the view still
    # shows something sensible.
    if not batches and (product.current_stock or 0) > 0:
        qty = product.current_stock
        profit = p_sell - p_cost
        batches.append({
            "cost": p_cost, "sell": p_sell, "qty": qty,
            "profit_unit": profit, "margin": (profit / p_sell * 100) if p_sell else _D(0),
            "stock_value": p_cost * qty, "profit_potential": profit * qty,
        })
    tot_qty = sum((b["qty"] for b in batches), _D(0))
    tot_cost = sum((b["stock_value"] for b in batches), _D(0))
    tot_sell = sum((b["sell"] * b["qty"] for b in batches), _D(0))
    tot_profit = sum((b["profit_potential"] for b in batches), _D(0))
    avg = None
    if tot_qty:
        avg_cost = tot_cost / tot_qty
        avg_sell = tot_sell / tot_qty
        avg = {
            "qty": tot_qty, "avg_cost": avg_cost, "avg_sell": avg_sell,
            "profit_unit": avg_sell - avg_cost,
            "margin": ((avg_sell - avg_cost) / avg_sell * 100) if avg_sell else _D(0),
            "stock_value": tot_cost, "profit_potential": tot_profit,
        }

    return render(request, "web/product_profile.html", {
        "active": "products", "product": product,
        "units": page, "page_obj": page,
        "in_stock_units": in_stock, "sold_units": sold, "sold_qty": sold_qty,
        "batches": batches, "batch_avg": avg,
    })


@shop_member_required
def barcodes(request):
    """The barcode table: every per-unit barcode with its own purchase price,
    selling price and warranty duration — exactly what a POS scan resolves."""
    from catalog.models import ProductUnit
    qs = ProductUnit.objects.select_related("product").order_by("-created_at")
    q = request.GET.get("q", "").strip()
    if q:
        from django.db.models import Q
        qs = qs.filter(Q(barcode__icontains=q) | Q(product__name__icontains=q))
    status = request.GET.get("status", "")
    if status in dict(ProductUnit.Status.choices):
        qs = qs.filter(status=status)
    page = _paginate(request, qs, per_page=30)
    return render(request, "web/barcodes.html", {
        "active": "barcodes", "units": page, "page_obj": page, "q": q, "status": status,
    })


@shop_member_required
@perm_required("manage_products")
def barcode_edit(request, pk):
    """Fix a wrong barcode (and optionally its price/warranty) on a single unit."""
    from catalog.models import ProductUnit
    unit = get_object_or_404(ProductUnit.objects, pk=pk)
    if request.method == "POST":
        new_code = (request.POST.get("barcode") or "").strip()
        if not new_code:
            messages.error(request, "Barcode cannot be empty.")
            return redirect("web:barcodes")
        # Block collision with a different unit's barcode in this shop.
        clash = ProductUnit.objects.filter(barcode=new_code).exclude(pk=unit.pk).exists()
        if clash:
            messages.error(request, f"Barcode “{new_code}” is already used by another item.")
            return redirect("web:barcodes")
        unit.barcode = _clip(new_code, 100)
        if request.POST.get("selling_price"):
            unit.selling_price = _dec_nn(request.POST.get("selling_price"))
        if request.POST.get("warranty_months") != "":
            unit.warranty_months = int(request.POST.get("warranty_months") or 0)
        unit.save(update_fields=["barcode", "selling_price", "warranty_months", "updated_at"])
        messages.success(request, "Barcode updated.")
    return redirect("web:barcodes")


@shop_member_required
@perm_required("manage_products")
def product_edit(request, pk):
    product = get_object_or_404(Product.objects, pk=pk)
    if request.method == "POST":
        new_barcode = request.POST.get("barcode", "").strip()
        if new_barcode and _barcode_taken(request.user.shop, new_barcode, exclude_product_pk=product.pk):
            messages.error(request, f"Barcode “{new_barcode}” is already in use.")
            return render(request, "web/product_edit.html", {"active": "products", "product": product})
        name = request.POST.get("name", "").strip()
        if not name:
            messages.error(request, "Product name is required.")
            return render(request, "web/product_edit.html", {"active": "products", "product": product})
        new_sku = request.POST.get("sku", "").strip()
        if new_sku and Product.all_objects.filter(
            shop_id=request.user.shop.id, sku=new_sku
        ).exclude(pk=product.pk).exists():
            messages.error(request, f"SKU “{new_sku}” is already in use.")
            return render(request, "web/product_edit.html", {"active": "products", "product": product})
        product.name = _clip(name, 200)
        product.sku = _clip(new_sku, 60)
        product.barcode = _clip(new_barcode, 60)
        product.cost_price = _dec_nn(request.POST.get("cost_price"))
        product.selling_price = _dec_nn(request.POST.get("selling_price"))
        product.reorder_level = _dec_nn(request.POST.get("reorder_level"))
        product.is_active = request.POST.get("is_active") == "on"
        # Category: prefer the chosen subcategory, else the parent category.
        category_raw = request.POST.get("subcategory") or request.POST.get("category") or None
        category_id = None if category_raw in ("", "0", None) else _int_or_none(category_raw)
        # Ignore ids that aren't this shop's (avoid FK 500 / cross-tenant link).
        if category_id and not Category.objects.filter(pk=category_id).exists():
            category_id = None
        brand_id = _int_or_none(request.POST.get("brand"))
        if brand_id and not Brand.objects.filter(pk=brand_id).exists():
            brand_id = None
        product.category_id = category_id
        product.brand_id = brand_id
        product.warranty_months = int(request.POST.get("warranty_months") or 0)
        product.save()
        # Keep the product-level Warranty row in sync when a duration is set.
        if product.warranty_months > 0:
            Warranty.objects.update_or_create(
                shop=request.user.shop, product=product, customer=None, serial_no="",
                defaults={"period_months": product.warranty_months},
            )
        messages.success(request, "Product updated.")
        return redirect("web:products")

    parent_categories = Category.objects.filter(parent__isnull=True).order_by("name")
    subcategories = list(
        Category.objects.filter(parent__isnull=False).values("id", "name", "parent_id")
    )
    # Preselect the product's current category (a subcategory carries a parent).
    cur = product.category
    sel_parent = (cur.parent_id if cur and cur.parent_id else (cur.id if cur else None))
    sel_sub = (cur.id if cur and cur.parent_id else None)
    return render(request, "web/product_edit.html", {
        "active": "products", "product": product,
        "parent_categories": parent_categories, "subcategories": subcategories,
        "brands": Brand.objects.order_by("name"),
        "sel_parent": sel_parent, "sel_sub": sel_sub,
    })


@shop_member_required
@perm_required("manage_products")
@require_http_methods(["POST"])
def set_barcode(request):
    """Assign/replace a product's barcode manually (from the purchase page)."""
    payload = _json_body(request)
    if payload is None:
        return JsonResponse({"error": "Invalid request."}, status=400)
    product = Product.objects.filter(pk=_int_or_none(payload.get("product"))).first()
    if product is None:
        return JsonResponse({"error": "Product not found."}, status=400)
    barcode = (payload.get("barcode") or "").strip()
    if not barcode:
        return JsonResponse({"error": "Barcode is required."}, status=400)
    # Unique per shop — check both product barcodes and per-unit barcodes.
    if _barcode_taken(request.user.shop, barcode, exclude_product_pk=product.pk):
        return JsonResponse({"error": f"Barcode “{barcode}” is already in use."}, status=400)
    product.barcode = barcode
    product.save(update_fields=["barcode"])
    return JsonResponse({"id": product.id, "barcode": barcode})


@shop_member_required
def item_lookup(request):
    """Scan a barcode/SKU → item history. If the product was sold before, show a
    'previously sold' page: latest sale, supplier/sourcing, warranty expiry, and a
    stock→sale timeline. NOTE: barcodes are per-product (no per-unit serials), so
    'sold' means the product has prior sale history, and we show the LATEST sale."""
    from dateutil.relativedelta import relativedelta
    from purchasing.models import PurchaseOrderItem

    code = (request.GET.get("barcode") or "").strip()
    ctx = {"active": "item_lookup", "code": code, "product": None, "not_found": False}
    if not code:
        return render(request, "web/item_lookup.html", ctx)

    from django.db.models import Q
    product = Product.objects.filter(Q(barcode=code) | Q(sku=code)).first()
    if product is None:
        ctx["not_found"] = True
        return render(request, "web/item_lookup.html", ctx)

    today = timezone.localdate()
    # Latest sale line for this product (the "previously sold" signal).
    last_item = (SaleItem.objects.filter(product=product)
                 .select_related("sale", "sale__customer")
                 .exclude(sale__status=Sale.Status.CANCELLED)
                 .order_by("-sale__sale_date").first())
    last_sale = last_item.sale if last_item else None
    days_since_sale = (today - last_sale.sale_date.date()).days if last_sale else None

    # Sourcing: product supplier + latest purchase order line.
    last_po_item = (PurchaseOrderItem.objects.filter(product=product)
                    .select_related("purchase_order").order_by("-created_at").first())

    # Warranty expiry: explicit Warranty row wins; else last sale + warranty_months.
    warranty = Warranty.objects.filter(product=product).order_by("-expiry_date").first()
    expiry = warranty.expiry_date if warranty else None
    if expiry is None and last_sale and product.warranty_months:
        expiry = last_sale.sale_date.date() + relativedelta(months=product.warranty_months)
    days_remaining = (expiry - today).days if expiry else None

    # Timeline: first inbound movement + the sale.
    first_in = (StockMovement.objects.filter(
        product=product, movement_type__in=[MovementType.PURCHASE_IN, MovementType.OPENING])
        .order_by("created_at").first())

    ctx.update({
        "product": product, "sold": last_sale is not None,
        "last_item": last_item, "last_sale": last_sale, "days_since_sale": days_since_sale,
        "supplier": product.supplier, "last_po": last_po_item.purchase_order if last_po_item else None,
        "warranty": warranty, "serial_no": warranty.serial_no if warranty else "",
        "expiry": expiry, "days_remaining": days_remaining,
        "first_in": first_in,
    })
    return render(request, "web/item_lookup.html", ctx)


@shop_member_required
@perm_required("manage_inventory")
def purchase_scan(request):
    """Item 7 — purchase products by scanning: add a product first, scan each
    unit's barcode (success/error feedback), then Entry pushes all into stock."""
    if request.method == "POST":  # Push to stock: apply queued quantities.
        payload = _json_body(request)
        if payload is None:
            return JsonResponse({"error": "Invalid request."}, status=400)
        supplier_id = _int_or_none(payload.get("supplier"))
        if supplier_id and not Supplier.objects.filter(pk=supplier_id).exists():
            supplier_id = None
        received, units = 0, Decimal("0")
        rejected = []          # duplicate unit barcodes (skipped)
        seen = set()           # dupes within this same submission
        purchase_total = Decimal("0")   # Σ qty × cost across this purchase
        for row in payload.get("items", []):
            product = Product.objects.filter(pk=_int_or_none(row.get("product"))).first()
            if product is None:
                continue
            qty = _dec(row.get("quantity", 0))
            if qty <= 0:
                continue
            # Cost defaults to the product's current cost when not supplied.
            cost = _dec(row.get("cost"), str(product.cost_price or 0))
            try:
                restock(
                    product=product, quantity=qty, unit_cost=cost,
                    note="Purchase", created_by=request.user,
                )
            except ValueError:
                continue
            purchase_total += qty * cost
            # Optional selling-price update from the pricing panel. This becomes
            # the product's DEFAULT price, but each unit below also snapshots THIS
            # batch's price so an older batch keeps selling at its own price.
            selling = row.get("selling")
            batch_selling = _dec(selling) if selling not in (None, "") else product.selling_price
            if selling not in (None, ""):
                product.selling_price = _dec(selling)
                product.save(update_fields=["selling_price"])
            # Optional warranty-duration override from the purchase page.
            warranty_in = row.get("warranty")
            if warranty_in not in (None, ""):
                try:
                    product.warranty_months = max(0, int(warranty_in))
                    product.save(update_fields=["warranty_months"])
                except (TypeError, ValueError):
                    pass
            if supplier_id and product.supplier_id != supplier_id:
                product.supplier_id = supplier_id
                product.save(update_fields=["supplier"])
            # Persist each scanned unit barcode so it's searchable + trackable
            # (features #6, #7). Barcodes are unique per shop — a duplicate
            # (against any product barcode, existing unit, or earlier in this
            # batch) is rejected and reported, not silently swallowed.
            from catalog.models import ProductUnit
            from service.models import Warranty
            months = product.warranty_months or 0
            for bc in row.get("barcodes", []):
                bc = (bc or "").strip()
                if not bc:
                    continue
                if bc in seen or _barcode_taken(request.user.shop, bc):
                    rejected.append(bc)
                    continue
                seen.add(bc)
                # Snapshot THIS batch's cost + selling + warranty onto the unit so
                # a POS scan of this barcode resolves everything from this one row.
                unit = ProductUnit.objects.create(
                    shop=request.user.shop, product=product, barcode=bc,
                    cost_price=cost, selling_price=batch_selling, warranty_months=months,
                )
                # Per-unit warranty so a scanned unit later resolves its coverage
                # (buyer + expiry get bound at sale time). Only for products that
                # actually offer a warranty.
                if months > 0:
                    Warranty.objects.create(
                        shop=request.user.shop, product=product, product_unit=unit,
                        serial_no=bc, period_months=months, start_date=timezone.localdate(),
                    )
            received += 1
            units += qty

        # Supplier payable + payment for this purchase (handled from this page):
        # we owe the full purchase; any amount paid now reduces the due and posts
        # a cash outflow. Needs a supplier to attribute the payable to.
        due_recorded = None
        if supplier_id and purchase_total > 0:
            from purchasing.models import Supplier as _Supplier
            from purchasing.services import pay_supplier
            supplier = _Supplier.objects.filter(pk=supplier_id).first()
            if supplier is not None:
                supplier.due_balance = (supplier.due_balance or Decimal("0")) + purchase_total
                supplier.save(update_fields=["due_balance"])
                paid = _dec(payload.get("paid", 0))
                if paid > 0:
                    pay_supplier(
                        supplier=supplier, amount=min(paid, purchase_total),
                        method=payload.get("method", "cash"), created_by=request.user,
                    )
                due_recorded = str(purchase_total - min(max(paid, Decimal("0")), purchase_total))
        return JsonResponse({
            "count": received, "units": str(units), "rejected": rejected,
            "total": str(purchase_total), "due": due_recorded,
        })
    return render(request, "web/purchase_scan.html", {
        "active": "purchase",
        "products": Product.objects.filter(is_active=True).order_by("name"),
        "suppliers": Supplier.objects.order_by("name"),
    })


@shop_member_required
@perm_required("manage_products")
@require_http_methods(["POST"])
def product_import(request):
    """Bulk import products from CSV — columns: product name, avg cost (item 9)."""
    import csv
    import io
    f = request.FILES.get("file")
    if not f:
        messages.error(request, "Choose a CSV file first.")
        return redirect("web:products")
    if f.size > 10 * 1024 * 1024:  # 10 MB cap — guard against huge-file uploads
        messages.error(request, "File is too large (max 10 MB).")
        return redirect("web:products")
    if not f.name.lower().endswith(".csv"):
        messages.error(request, "Upload a .csv file.")
        return redirect("web:products")
    shop = request.user.shop
    reader = csv.reader(io.TextIOWrapper(f.file, encoding="utf-8-sig", errors="ignore"))
    created, n = 0, Product.all_objects.filter(shop_id=shop.id).count()
    for row in reader:
        if not row:
            continue
        name = (row[0] or "").strip()
        if not name:
            continue
        try:  # a non-numeric cost cell (e.g. the header row) is skipped
            cost = Decimal(str(row[1]).strip()) if len(row) > 1 and str(row[1]).strip() else Decimal("0")
        except (InvalidOperation, ValueError):
            continue
        n += 1
        sku = f"SKU-{n:05d}"
        while Product.all_objects.filter(shop_id=shop.id, sku=sku).exists():
            n += 1
            sku = f"SKU-{n:05d}"
        Product.objects.create(shop=shop, name=_clip(name, 200), cost_price=cost, sku=sku)
        created += 1
    messages.success(request, f"Imported {created} product(s).")
    return redirect("web:products")


@shop_member_required
@perm_required("manage_products")
@require_http_methods(["POST"])
def product_delete(request, pk):
    """Delete a product. Blocked (PROTECT) if it has sales/purchase history —
    tell the user to deactivate instead."""
    product = get_object_or_404(Product.objects, pk=pk)
    name = product.name
    try:
        product.delete()
        messages.success(request, f"Deleted “{name}”.")
    except ProtectedError:
        messages.error(request, f"“{name}” has sales or purchase history — deactivate it instead of deleting.")
    return redirect("web:products")


@shop_member_required
@perm_required("manage_products")
@require_http_methods(["POST"])
def product_toggle(request, pk):
    """Active/inactive switch (item 5)."""
    product = get_object_or_404(Product.objects, pk=pk)
    product.is_active = not product.is_active
    product.save(update_fields=["is_active"])
    if request.headers.get("HX-Request") or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"is_active": product.is_active})
    return redirect(request.META.get("HTTP_REFERER", "web:products"))


@shop_member_required
@perm_required("manage_inventory")
@require_http_methods(["POST"])
def product_restock(request, pk):
    """Re-inventory at a (possibly new) cost; rolls a weighted-average cost (item 6)."""
    product = get_object_or_404(Product.objects, pk=pk)
    try:
        result = restock(
            product=product, quantity=_dec(request.POST.get("quantity")),
            unit_cost=_dec_nn(request.POST.get("unit_cost")),
            note=request.POST.get("note", ""), created_by=request.user,
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect(request.META.get("HTTP_REFERER", "web:products"))
    diff = result["price_difference"]
    sign = "↑" if diff > 0 else ("↓" if diff < 0 else "→")
    messages.success(
        request,
        f"Restocked {product.name}. Cost {result['old_cost']} {sign} avg {result['new_avg_cost']} "
        f"(this batch @ {result['new_purchase_cost']}, diff {diff}).",
    )
    return redirect(request.META.get("HTTP_REFERER", "web:products"))


# --- Inventory --------------------------------------------------------------

@shop_member_required
def inventory(request):
    if request.method == "POST":
        if not request.user.has_perm_code("manage_inventory"):
            messages.error(request, "Not allowed.")
            return redirect("web:inventory")
        product = get_object_or_404(Product.objects, pk=_int_or_none(request.POST.get("product")))
        apply_movement(
            shop=request.user.shop, product=product,
            movement_type=request.POST["movement_type"],
            quantity=_dec(request.POST.get("quantity")),
            note=request.POST.get("note", ""), created_by=request.user,
        )
        messages.success(request, "Stock adjusted.")
        return redirect("web:inventory")
    return render(request, "web/inventory.html", {
        "active": "inventory",
        "products": Product.objects.order_by("name"),
        "movements": _paginate(request, StockMovement.objects.select_related("product")),
        "page_obj": _paginate(request, StockMovement.objects.select_related("product")),
        "adjust_types": [MovementType.ADJUST_IN, MovementType.ADJUST_OUT,
                         MovementType.DAMAGE_OUT, MovementType.OPENING],
    })


# --- POS --------------------------------------------------------------------

@shop_member_required
@perm_required("create_sale")
def pos(request):
    return render(request, "web/pos.html", {
        "active": "pos",
        "products": Product.objects.filter(is_active=True).order_by("name"),
        "customers": Customer.objects.order_by("name"),
    })


@shop_member_required
@perm_required("create_sale")
@require_http_methods(["POST"])
def pos_checkout(request):
    """JSON checkout from the Alpine POS cart (session-auth + CSRF)."""
    payload = _json_body(request)
    if payload is None:
        return JsonResponse({"error": "Invalid request."}, status=400)
    items = []
    for row in payload.get("items", []):
        product = Product.objects.filter(pk=_int_or_none(row.get("product"))).first()
        if product is None:  # scoped: another shop's id resolves to None
            return JsonResponse({"error": "Invalid product."}, status=400)
        qty = _dec(row.get("quantity", 1))
        if qty <= 0:
            return JsonResponse({"error": "Quantity must be greater than zero."}, status=400)
        items.append({"product": product, "quantity": qty,
                      "unit_price": _dec_nn(row.get("unit_price", product.selling_price))})
    if not items:
        return JsonResponse({"error": "Cart is empty."}, status=400)

    customer = Customer.objects.filter(pk=_int_or_none(payload.get("customer"))).first()
    # Item 2: customer info + payment amount are required. A saved customer
    # supplies name/phone; otherwise walk-in name AND phone must be entered.
    cust_name = payload.get("customer_name", "").strip()
    cust_phone = payload.get("customer_phone", "").strip()
    cust_address = payload.get("customer_address", "").strip()
    if customer is None and not (cust_name and cust_phone):
        return JsonResponse({"error": "Customer name and phone are required."}, status=400)
    if payload.get("paid") in (None, ""):
        return JsonResponse({"error": "Payment amount is required."}, status=400)

    # Persist walk-in info into the Customers table: reuse an existing record
    # with the same phone, else create one. This makes every POS buyer show up
    # under Customers instead of living only on the sale.
    if customer is None:
        customer = Customer.objects.filter(phone=cust_phone).first()
        if customer is None:
            customer = Customer.objects.create(
                shop=request.user.shop, name=_clip(cust_name, 150),
                phone=_clip(cust_phone, 30), address=cust_address,
            )

    payments = [{"amount": _dec(payload["paid"]), "method": payload.get("method", "cash")}]
    try:
        sale = create_sale(shop=request.user.shop, customer=customer, items=items,
                           payments=payments, discount=_dec(payload.get("discount", 0)),
                           created_by=request.user,
                           idempotency_key=str(payload.get("idempotency_key", ""))[:64])
    except ValueError as exc:  # out of stock / insufficient stock (item 7)
        return JsonResponse({"error": str(exc)}, status=400)
    # (unit-sold + warranty binding now handled inside create_sale)
    return JsonResponse({"invoice_no": sale.invoice_no, "total": str(sale.total),
                         "paid": str(sale.paid), "status": sale.status, "id": sale.id,
                         "print_url": reverse("web:sale_print", args=[sale.id])})


@shop_member_required
@perm_required("create_sale")
@require_http_methods(["POST"])
def pos_add_customer(request):
    """Create a Customer inline from the POS screen (item 8). Returns id+name."""
    payload = _json_body(request)
    if payload is None:
        return JsonResponse({"error": "Invalid request."}, status=400)
    name = (payload.get("name") or "").strip()
    phone = (payload.get("phone") or "").strip()
    if not name or not phone:
        return JsonResponse({"error": "Name and phone are required."}, status=400)
    customer = Customer.objects.create(
        shop=request.user.shop, name=_clip(name, 150), phone=_clip(phone, 30),
        address=(payload.get("address") or "").strip(),
    )
    return JsonResponse({"id": customer.id, "name": customer.name})


# --- POS multi-step flow: cart page -> customer/payment page -> invoice ------

@shop_member_required
@perm_required("create_sale")
@require_http_methods(["POST"])
def pos_cart_save(request):
    """Save the built cart to the session, then hand off to the customer page."""
    payload = _json_body(request)
    if payload is None:
        return JsonResponse({"error": "Invalid request."}, status=400)
    items = []
    for row in payload.get("items", []):
        product = Product.objects.filter(pk=_int_or_none(row.get("product"))).first()
        if product is None:
            return JsonResponse({"error": "Invalid product."}, status=400)
        qty = _dec(row.get("quantity", 1))
        if qty <= 0:
            return JsonResponse({"error": "Quantity must be greater than zero."}, status=400)
        items.append({"product": product.id, "quantity": str(qty),
                      "unit_price": str(_dec_nn(row.get("unit_price", product.selling_price)))})
    if not items:
        return JsonResponse({"error": "Cart is empty."}, status=400)
    request.session["pos_cart"] = items
    # One idempotency token per cart hand-off. If the customer/payment form is
    # then double-submitted (double-click / refresh), create_sale sees the same
    # key and returns the first sale instead of ringing it up twice.
    request.session["pos_txn_key"] = uuid.uuid4().hex
    return JsonResponse({"next": reverse("web:pos_customer")})


@shop_member_required
@perm_required("create_sale")
def pos_customer(request):
    """Step 2: fill customer info + payment for the session cart, then finalize
    the sale and redirect to the printable invoice."""
    cart = request.session.get("pos_cart", [])
    if not cart:
        messages.error(request, "Cart is empty — add products first.")
        return redirect("web:pos")

    if request.method == "POST":
        customer = Customer.objects.filter(pk=_int_or_none(request.POST.get("customer"))).first()
        cust_name = request.POST.get("customer_name", "").strip()
        cust_phone = request.POST.get("customer_phone", "").strip()
        if customer is None and not (cust_name and cust_phone):
            messages.error(request, "Customer name and phone are required.")
            return redirect("web:pos_customer")
        if request.POST.get("paid", "") == "":
            messages.error(request, "Payment amount is required.")
            return redirect("web:pos_customer")
        if customer is None:
            customer = Customer.objects.filter(phone=cust_phone).first() or Customer.objects.create(
                shop=request.user.shop, name=_clip(cust_name, 150), phone=_clip(cust_phone, 30),
                address=request.POST.get("customer_address", "").strip(),
            )
        items = []
        for row in cart:
            product = Product.objects.filter(pk=_int_or_none(row.get("product"))).first()
            if product is not None:
                items.append({"product": product, "quantity": _dec(row["quantity"]),
                              "unit_price": _dec(row["unit_price"])})
        if not items:
            request.session.pop("pos_cart", None)
            messages.error(request, "Cart products are no longer available.")
            return redirect("web:pos")
        try:
            sale = create_sale(
                shop=request.user.shop, customer=customer, items=items,
                payments=[{"amount": _dec(request.POST.get("paid")), "method": request.POST.get("method", "cash")}],
                discount=_dec(request.POST.get("discount", 0)), created_by=request.user,
                idempotency_key=request.session.get("pos_txn_key", ""),
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("web:pos_customer")
        # (unit-sold + warranty binding now handled inside create_sale)
        request.session.pop("pos_cart", None)
        request.session.pop("pos_txn_key", None)
        messages.success(request, f"Sale {sale.invoice_no} completed.")
        return redirect("web:sale_print", pk=sale.id)

    # GET — build a display cart summary.
    disp, total = [], Decimal("0")
    for row in cart:
        product = Product.objects.filter(pk=_int_or_none(row.get("product"))).first()
        if product is None:
            continue
        qty, price = _dec(row["quantity"]), _dec(row["unit_price"])
        line = qty * price
        total += line
        disp.append({"name": product.name, "quantity": qty, "unit_price": price, "line": line})
    return render(request, "web/pos_customer.html", {
        "active": "pos", "cart": disp, "total": total,
        "customers": Customer.objects.order_by("name"),
    })


# --- Sales ------------------------------------------------------------------


@shop_member_required
@perm_required("view_reports")
def sold_products(request):
    """Item 3: report of products actually sold, with qty / revenue / profit."""
    shop = request.user.shop
    days = int(request.GET.get("days", 30) or 30)
    start = timezone.now() - timedelta(days=days)
    rows = top_products(shop, start=start, end=timezone.now(), limit=500)
    return render(request, "web/sold_products.html", {
        "active": "sold_products", "rows": rows, "days": days,
        "total_qty": sum(r["qty"] for r in rows),
        "total_revenue": sum(r["revenue"] for r in rows),
        "total_profit": sum(r["profit"] for r in rows),
    })


@shop_member_required
@perm_required("view_reports")
def selling_details(request):
    """Per-line selling details: every product sold, one row each, with the
    customer it was sold to (saved customer or walk-in captured at POS)."""
    days = int(request.GET.get("days", 30) or 30)
    start = timezone.now() - timedelta(days=days)
    items = (
        SaleItem.objects.select_related("sale", "sale__customer", "product")
        .exclude(sale__status=Sale.Status.CANCELLED)
        .filter(sale__sale_date__gte=start)
        .order_by("-sale__sale_date")
    )
    page = _paginate(request, items)
    return render(request, "web/selling_details.html", {
        "active": "selling_details", "items": page, "page_obj": page, "days": days,
    })


@shop_member_required
def sales(request):
    qs = Sale.objects.select_related("customer").order_by("-sale_date")
    if status := request.GET.get("status"):
        qs = qs.filter(status=status)
    page = _paginate(request, qs)
    return render(request, "web/sales.html", {"active": "sales", "sales": page, "page_obj": page})


@shop_member_required
def sale_detail(request, pk):
    sale = get_object_or_404(Sale.objects.prefetch_related("items", "payments"), pk=pk)
    if request.method == "POST" and request.POST.get("action") == "add_payment":
        try:
            add_payment(sale=sale, amount=_dec(request.POST.get("amount")),
                        method=request.POST.get("method", "cash"), created_by=request.user)
            messages.success(request, "Payment recorded.")
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect("web:sale_detail", pk=pk)
    return render(request, "web/sale_detail.html", {"active": "sales", "sale": sale})


@shop_member_required
@perm_required("delete_sale")
def sale_edit(request, pk):
    """Edit a completed sale's line items + discount (item 12).

    Destructive to a finalized financial record (can reduce quantities, change
    prices, effectively void lines), so it requires ``delete_sale`` — not the
    ``create_sale`` a cashier holds."""
    sale = get_object_or_404(Sale.objects.prefetch_related("items"), pk=pk)
    if request.method == "POST":
        # Optimistic lock: the form carries the sale's updated_at at render time.
        # If it no longer matches, someone else edited the sale in between — reject
        # rather than silently clobbering their change (lost update).
        posted_version = request.POST.get("version", "")
        if posted_version and posted_version != sale.updated_at.isoformat():
            messages.error(request, "This sale was changed in another window. Reloaded — review and try again.")
            return redirect("web:sale_edit", pk=sale.id)
        products = request.POST.getlist("product")
        quantities = request.POST.getlist("quantity")
        prices = request.POST.getlist("unit_price")
        items = []
        for pid, qty, price in zip(products, quantities, prices):
            if not pid or not qty:
                continue
            product = Product.objects.filter(pk=pid).first()
            if product is None:
                continue
            items.append({"product": product, "quantity": _dec(qty), "unit_price": _dec(price)})
        try:
            edit_sale(sale=sale, items=items, discount=_dec(request.POST.get("discount", 0)),
                      created_by=request.user)
            messages.success(request, f"Sale {sale.invoice_no} updated.")
            return redirect("web:sale_detail", pk=sale.id)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("web:sale_edit", pk=sale.id)
    return render(request, "web/sale_edit.html", {
        "active": "sales", "sale": sale,
        "products": Product.objects.filter(is_active=True).order_by("name"),
        "version": sale.updated_at.isoformat(),
    })


@shop_member_required
def sale_print(request, pk):
    """Printable invoice document (item 1) — clean layout with the shop logo."""
    sale = get_object_or_404(Sale.objects.prefetch_related("items", "payments"), pk=pk)
    return render(request, "web/invoice_print.html", {"sale": sale, "shop": request.user.shop})


# --- Customers --------------------------------------------------------------

@shop_member_required
@perm_required("manage_customers")
def customers(request):
    if request.method == "POST":
        if not request.user.has_perm_code("manage_customers"):
            messages.error(request, "Not allowed.")
            return redirect("web:customers")
        action = request.POST.get("action", "add")
        if action == "delete":
            customer = get_object_or_404(Customer.objects, pk=_int_or_none(request.POST.get("id")))
            name = customer.name
            customer.delete()  # sales/tickets/warranties FK set null on delete
            messages.success(request, f"Deleted {name}.")
            return redirect("web:customers")
        if action == "edit":
            customer = get_object_or_404(Customer.objects, pk=_int_or_none(request.POST.get("id")))
            new_name = request.POST.get("name", "").strip()
            if not new_name:
                messages.error(request, "Customer name is required.")
                return redirect("web:customers")
            cust_email = request.POST.get("email", "").strip()
            if cust_email and not _valid_email(cust_email):
                messages.error(request, "Enter a valid email address.")
                return redirect("web:customers")
            customer.name = _clip(new_name, 150)
            customer.phone = _clip(request.POST.get("phone", ""), 30)
            customer.email = _clip(cust_email, 254)
            customer.address = request.POST.get("address", "")
            customer.discount_percent = _clamp_pct(request.POST.get("discount_percent"))
            customer.save(update_fields=["name", "phone", "email", "address", "discount_percent"])
            messages.success(request, "Customer updated.")
            return redirect("web:customers")
        name = request.POST.get("name", "").strip()
        if not name:
            messages.error(request, "Customer name is required.")
            return redirect("web:customers")
        cust_email = request.POST.get("email", "").strip()
        if cust_email and not _valid_email(cust_email):
            messages.error(request, "Enter a valid email address.")
            return redirect("web:customers")
        Customer.objects.create(
            shop=request.user.shop, name=_clip(name, 150),
            phone=_clip(request.POST.get("phone", ""), 30),
            email=_clip(cust_email, 254),
            address=request.POST.get("address", ""),
            discount_percent=_clamp_pct(request.POST.get("discount_percent")),
            whatsapp_consent=request.POST.get("whatsapp_consent") == "on",
        )
        messages.success(request, "Customer added.")
        return redirect("web:customers")
    q = request.GET.get("q", "")
    qs = Customer.objects.order_by("name")
    if q:
        from django.db.models import Q
        qs = qs.filter(Q(name__icontains=q) | Q(phone__icontains=q))
    page = _paginate(request, qs)
    return render(request, "web/customers.html", {"active": "customers", "customers": page, "page_obj": page, "q": q})


# --- Dues management --------------------------------------------------------

OPEN_SALE_STATUSES = [Sale.Status.DUE, Sale.Status.PARTIAL, Sale.Status.PARTIALLY_RETURNED]


@shop_member_required
def dues(request):
    """Receivables (customers owe us) + payables (we owe suppliers), with
    inline collect / pay actions."""
    from django.db.models import Count, Min, Q, Sum

    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "collect":
                if not request.user.has_perm_code("manage_customers"):
                    raise PermissionError("Not allowed to collect customer dues.")
                customer = get_object_or_404(Customer.objects, pk=_int_or_none(request.POST.get("customer_id")))
                collect_customer_due(
                    customer=customer, amount=_dec(request.POST.get("amount")),
                    method=request.POST.get("method", "cash"), created_by=request.user,
                )
                messages.success(request, f"Collected from {customer.name}.")
            elif action == "pay_supplier":
                if not request.user.has_perm_code("manage_purchasing"):
                    raise PermissionError("Not allowed to pay suppliers.")
                supplier = get_object_or_404(Supplier.objects, pk=_int_or_none(request.POST.get("supplier_id")))
                pay_supplier(
                    supplier=supplier, amount=_dec(request.POST.get("amount")),
                    method=request.POST.get("method", "cash"),
                    reference=request.POST.get("reference", ""), created_by=request.user,
                )
                messages.success(request, f"Paid {supplier.name}.")
        except (ValueError, PermissionError) as exc:
            messages.error(request, str(exc))
        return redirect("web:dues")

    receivables = (
        Customer.objects.filter(due_balance__gt=0)
        .annotate(
            open_invoices=Count("sales", filter=Q(sales__status__in=OPEN_SALE_STATUSES)),
            oldest_due=Min("sales__sale_date", filter=Q(sales__status__in=OPEN_SALE_STATUSES)),
        )
        .order_by("-due_balance")
    )
    payables = Supplier.objects.filter(due_balance__gt=0).order_by("-due_balance")
    return render(request, "web/dues.html", {
        "active": "dues",
        "receivables": receivables,
        "payables": payables,
        "total_receivable": Customer.objects.filter(due_balance__gt=0)
            .aggregate(s=Sum("due_balance"))["s"] or 0,
        "total_payable": Supplier.objects.filter(due_balance__gt=0)
            .aggregate(s=Sum("due_balance"))["s"] or 0,
    })


# --- Suppliers & Purchases --------------------------------------------------

@shop_member_required
@perm_required("manage_purchasing")
def suppliers(request):
    if request.method == "POST":
        action = request.POST.get("action", "add")
        if action == "delete":
            supplier = get_object_or_404(Supplier.objects, pk=_int_or_none(request.POST.get("id")))
            name = supplier.name
            try:
                supplier.delete()
                messages.success(request, f"Deleted {name}.")
            except ProtectedError:
                messages.error(request, f"{name} has purchase orders or payments — cannot delete.")
            return redirect("web:suppliers")
        if action == "edit":
            supplier = get_object_or_404(Supplier.objects, pk=_int_or_none(request.POST.get("id")))
            supplier.name = _clip(request.POST.get("name", supplier.name), 150)
            supplier.company_name = _clip(request.POST.get("company_name", ""), 180)
            supplier.phone = _clip(request.POST.get("phone", ""), 30)
            supplier.email = _clip(request.POST.get("email", ""), 254)
            supplier.save(update_fields=["name", "company_name", "phone", "email"])
            messages.success(request, "Supplier updated.")
            return redirect("web:suppliers")
        email = request.POST.get("email", "").strip()
        if email and not _valid_email(email):
            messages.error(request, "Enter a valid email address.")
            return redirect("web:suppliers")
        sup_name = request.POST.get("name", "").strip()
        if not sup_name:
            messages.error(request, "Supplier name is required.")
            return redirect("web:suppliers")
        Supplier.objects.create(shop=request.user.shop, name=_clip(sup_name, 150),
                                company_name=_clip(request.POST.get("company_name", ""), 180),
                                phone=_clip(request.POST.get("phone", ""), 30), email=_clip(email, 254))
        messages.success(request, "Supplier added.")
        return redirect(request.POST.get("next") or "web:suppliers")
    page = _paginate(request, Supplier.objects.order_by("name"))
    return render(request, "web/suppliers.html", {"active": "suppliers", "suppliers": page, "page_obj": page})


@shop_member_required
@perm_required("manage_purchasing")
def purchases(request):
    if request.method == "POST":
        # Inline "add supplier" on the same page (item 9).
        supplier_id = request.POST.get("supplier")
        if supplier_id == "__new__":
            new_name = request.POST.get("supplier_new", "").strip()
            if not new_name:
                messages.error(request, "Enter the new supplier name.")
                return redirect(request.POST.get("next") or "web:purchases")
            supplier = Supplier.objects.create(
                shop=request.user.shop, name=_clip(new_name, 150),
                company_name=_clip(request.POST.get("supplier_company", ""), 180),
                phone=_clip(request.POST.get("supplier_phone", ""), 30),
            )
        else:
            supplier = get_object_or_404(Supplier.objects, pk=supplier_id)
        product = get_object_or_404(Product.objects, pk=_int_or_none(request.POST.get("product")))
        quantity = _dec_nn(request.POST.get("quantity"))
        if quantity <= 0:
            messages.error(request, "Purchase quantity must be greater than zero.")
            return redirect(request.POST.get("next") or "web:purchases")
        po = create_purchase_order(
            shop=request.user.shop, supplier=supplier, created_by=request.user,
            items=[{"product": product, "quantity": quantity,
                    "unit_cost": _dec_nn(request.POST.get("unit_cost"))}],
        )
        if request.POST.get("receive") == "on":
            receive_purchase_order(po=po, paid=_dec_nn(request.POST.get("paid", 0)), created_by=request.user)
        messages.success(request, f"Purchase order {po.po_number} created.")
        return redirect(request.POST.get("next") or "web:purchases")
    page = _paginate(request, PurchaseOrder.objects.select_related("supplier").order_by("-created_at"))
    return render(request, "web/purchases.html", {
        "active": "purchases",
        "orders": page, "page_obj": page,
        "suppliers": Supplier.objects.order_by("name"),
        "products": Product.objects.order_by("name"),
    })


@shop_member_required
@perm_required("manage_purchasing")
def purchase_print(request, pk):
    """Printable purchase invoice — includes each product's warranty duration."""
    po = get_object_or_404(
        PurchaseOrder.objects.select_related("supplier").prefetch_related("items__product"), pk=pk
    )
    return render(request, "web/purchase_invoice_print.html", {"po": po, "shop": request.user.shop})


@shop_member_required
@perm_required("manage_purchasing")
@require_http_methods(["POST"])
def purchase_receive(request, pk):
    po = get_object_or_404(PurchaseOrder.objects, pk=pk)
    try:
        receive_purchase_order(po=po, paid=_dec(request.POST.get("paid", 0)), created_by=request.user)
        messages.success(request, "Received into stock.")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("web:purchases")


@shop_member_required
@perm_required("manage_purchasing")
@require_http_methods(["POST"])
def purchase_payment(request, pk):
    """Record a partial payment against a purchase order (feature #2)."""
    po = get_object_or_404(PurchaseOrder.objects, pk=pk)
    try:
        add_purchase_payment(
            po=po, amount=_dec(request.POST.get("amount", 0)),
            method=request.POST.get("method", "cash"),
            reference=request.POST.get("reference", ""),
            note=request.POST.get("note", ""), created_by=request.user,
        )
        messages.success(request, "Payment recorded.")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("web:purchases")


# --- Expenses ---------------------------------------------------------------

@shop_member_required
@perm_required("manage_expenses")
def expenses(request):
    if request.method == "POST":
        amount = _dec(request.POST.get("amount"))
        if amount <= 0:
            messages.error(request, "Expense amount must be greater than zero.")
            return redirect("web:expenses")
        spent_on_raw = request.POST.get("spent_on")
        spent_on = _parse_date(spent_on_raw) if spent_on_raw else timezone.localdate()
        if spent_on is None:
            messages.error(request, "Enter a valid date (YYYY-MM-DD).")
            return redirect("web:expenses")
        record_expense(
            shop=request.user.shop, amount=amount,
            spent_on=spent_on,
            category=ExpenseCategory.objects.filter(pk=_int_or_none(request.POST.get("category"))).first(),
            note=_clip(request.POST.get("note", ""), 255), created_by=request.user,
        )
        messages.success(request, "Expense recorded.")
        return redirect("web:expenses")
    page = _paginate(request, Expense.objects.select_related("category").order_by("-spent_on"))
    return render(request, "web/expenses.html", {
        "active": "expenses",
        "expenses": page, "page_obj": page,
        "categories": ExpenseCategory.objects.order_by("name"),
    })


# --- Accounting -------------------------------------------------------------

@shop_member_required
@perm_required("view_profit")
def accounting(request):
    """Income / expense / profit with daily/weekly/monthly/yearly filter (item 16)."""
    shop = request.user.shop
    start, end, period = _period_range(request.GET.get("period", "monthly"))
    summary = profit_summary(shop, start=start, end=end)
    flow = cash_flow(shop, start=start, end=end)
    # Per-day series for the chart within the range.
    trend = sales_trend(shop, days=max((end - start).days, 1))
    # Transaction flow — signed cash-ledger entries (income + / expense −).
    # An explicit from/to date range (day-by-day) overrides the period window.
    from accounting.models import LedgerEntry
    tx_from = _parse_date(request.GET.get("from"))
    tx_to = _parse_date(request.GET.get("to"))
    tx_start = timezone.make_aware(datetime.combine(tx_from, dt_time.min)) if tx_from else start
    tx_end = timezone.make_aware(datetime.combine(tx_to, dt_time.max)) if tx_to else end
    txns = LedgerEntry.objects.filter(
        created_at__gte=tx_start, created_at__lte=tx_end
    ).order_by("-created_at")
    page = _paginate(request, txns, per_page=25)
    return render(request, "web/accounting.html", {
        "active": "accounting", "period": period,
        "summary": summary, "cash_flow": flow, "position": financial_position(shop),
        "trend_labels": [str(r["day"]) for r in trend],
        "trend_values": [float(r["revenue"]) for r in trend],
        "range_start": start.date(), "range_end": end.date(),
        "transactions": page, "page_obj": page,
        "tx_from": tx_from.isoformat() if tx_from else "",
        "tx_to": tx_to.isoformat() if tx_to else "",
    })


# --- Reports ----------------------------------------------------------------

@shop_member_required
@perm_required("view_reports")
def reports(request):
    shop = request.user.shop
    now = timezone.now()
    tops = top_products(shop, limit=6)
    cat = sales_by_category(shop)
    ctx = {
        "active": "reports",
        "profit": profit_summary(shop, start=now - timedelta(days=30), end=now),
        "position": financial_position(shop),
        "cash_flow": cash_flow(shop, start=now - timedelta(days=30), end=now),
        "report_types": ["sales", "purchase", "inventory", "profit", "expense",
                         "customer_due", "supplier_due", "tax", "employee_sales"],
        # Chart data (item 15 — charts first, then downloads).
        "top_labels": [r["product__name"] for r in tops],
        "top_values": [float(r["revenue"]) for r in tops],
        "cat_labels": [(r["product__category__name"] or "Uncategorized") for r in cat],
        "cat_values": [float(r["revenue"]) for r in cat],
        # Repair-shop analytics bundle (revenue mix, TAT, device volume, issue
        # Pareto, stock turnover, reorder, customer acquisition).
        "charts": reports_charts(shop),
    }
    return render(request, "web/reports.html", ctx)


@shop_member_required
@perm_required("view_reports")
def report_export(request, rtype):
    from reports.datasets import BUILDERS
    from reports.exporters import export
    builder = BUILDERS.get(rtype)
    if builder is None:
        messages.error(request, "Unknown report.")
        return redirect("web:reports")
    fmt = request.GET.get("format", "csv")
    title, columns, rows = builder(request.user.shop)
    return export(fmt, title, columns, rows)


# --- Service ----------------------------------------------------------------

@shop_member_required
@perm_required("view_service")
def tickets(request):
    if request.method == "POST":
        if not request.user.has_perm_code("manage_service"):
            messages.error(request, "Not allowed.")
            return redirect("web:tickets")
        device_description = request.POST.get("device_description", "").strip()
        if not device_description:
            messages.error(request, "Device description is required.")
            return redirect("web:tickets")
        create_ticket(
            shop=request.user.shop,
            customer=Customer.objects.filter(pk=_int_or_none(request.POST.get("customer"))).first(),
            customer_name=_clip(request.POST.get("customer_name", ""), 150),
            customer_phone=_clip(request.POST.get("customer_phone", ""), 30),
            device_description=_clip(device_description, 200),
            # Only accept values that are real choices; anything else → blank so a
            # tampered dropdown can't poison analytics that key on the code.
            device_type=(request.POST.get("device_type", "") if request.POST.get("device_type", "") in ServiceTicket.DeviceType.values else ""),
            issue_type=(request.POST.get("issue_type", "") if request.POST.get("issue_type", "") in ServiceTicket.IssueType.values else ""),
            complaint=request.POST.get("complaint", ""),
            service_charge=_dec_nn(request.POST.get("service_charge")),
            estimated_delivery=_parse_date(request.POST.get("estimated_delivery")),
            warranty=Warranty.objects.filter(pk=_int_or_none(request.POST.get("warranty"))).first(),
            created_by=request.user,
        )
        messages.success(request, "Ticket created.")
        return redirect("web:tickets")
    ticket_page = _paginate(request, ServiceTicket.objects.select_related("customer", "warranty", "warranty__product").order_by("-received_at"))
    return render(request, "web/tickets.html", {
        "active": "tickets",
        "tickets": ticket_page, "page_obj": ticket_page,
        "customers": Customer.objects.order_by("name"),
        "warranties": Warranty.objects.select_related("product").order_by("-start_date")[:200],
        "statuses": ServiceTicket.Status.choices,
        "device_types": ServiceTicket.DeviceType.choices,
        "issue_types": ServiceTicket.IssueType.choices,
    })


@shop_member_required
@perm_required("manage_service")
@require_http_methods(["POST"])
def ticket_status(request, pk):
    ticket = get_object_or_404(ServiceTicket.objects, pk=pk)
    new_status = request.POST.get("status")
    if new_status in ServiceTicket.Status.values:
        change_ticket_status(ticket=ticket, new_status=new_status, changed_by=request.user)
        messages.success(request, "Status updated.")
    return redirect("web:tickets")


@shop_member_required
@perm_required("view_service")
def ticket_invoice(request, pk):
    """Printable service invoice/receipt handed to the customer on delivery (item 14)."""
    ticket = get_object_or_404(
        ServiceTicket.objects.prefetch_related("parts__product").select_related("customer"), pk=pk
    )
    return render(request, "web/service_invoice_print.html", {
        "ticket": ticket, "shop": request.user.shop,
        "parts_total": ticket.parts_total, "total": ticket.bill_total,
    })


@shop_member_required
@perm_required("view_service")
def ticket_detail(request, pk):
    """Ticket workbench: add parts (products) to the bill, set the service
    charge, and collect payments — with running total / paid / due."""
    ticket = get_object_or_404(
        ServiceTicket.objects.prefetch_related("parts__product").select_related("customer"), pk=pk
    )
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "add_part":
                if not request.user.has_perm_code("manage_service"):
                    raise PermissionError("Not allowed to edit tickets.")
                product = Product.objects.filter(pk=_int_or_none(request.POST.get("product"))).first()
                if product is None:
                    raise ValueError("Pick a valid product.")
                add_ticket_part(
                    ticket=ticket, product=product,
                    quantity=_dec_nn(request.POST.get("quantity", 1)) or Decimal("1"),
                    unit_price=_dec_nn(request.POST.get("unit_price")) if request.POST.get("unit_price") else None,
                    from_stock=request.POST.get("from_stock") == "on",
                    created_by=request.user,
                )
                messages.success(request, f"Added {product.name} to the ticket.")
            elif action == "remove_part":
                if not request.user.has_perm_code("manage_service"):
                    raise PermissionError("Not allowed to edit tickets.")
                ticket.parts.filter(pk=_int_or_none(request.POST.get("part_id"))).delete()
                messages.success(request, "Part removed.")
            elif action == "set_charge":
                if not request.user.has_perm_code("manage_service"):
                    raise PermissionError("Not allowed to edit tickets.")
                ticket.service_charge = _dec_nn(request.POST.get("service_charge"))
                ticket.save(update_fields=["service_charge", "updated_at"])
                messages.success(request, "Service charge updated.")
            elif action == "add_payment":
                add_ticket_payment(
                    ticket=ticket, amount=_dec(request.POST.get("amount")),
                    method=request.POST.get("method", "cash"), created_by=request.user,
                )
                messages.success(request, "Payment recorded.")
        except (ValueError, PermissionError) as exc:
            messages.error(request, str(exc))
        return redirect("web:ticket_detail", pk=ticket.id)

    return render(request, "web/ticket_detail.html", {
        "active": "tickets", "ticket": ticket,
        "products": Product.objects.filter(is_active=True).order_by("name"),
    })


@shop_member_required
@perm_required("view_service")
def warranties(request):
    if request.method == "POST":
        if not request.user.has_perm_code("manage_service"):
            messages.error(request, "Not allowed.")
            return redirect("web:warranties")
        # Warranty return / claim (item 10).
        if request.POST.get("action") == "claim":
            from service.models import Warranty as W, WarrantyClaim
            warranty = get_object_or_404(W.objects, pk=_int_or_none(request.POST.get("warranty_id")))
            issue = request.POST.get("issue_description", "")
            expected = _parse_date(request.POST.get("expected_return_date"))
            WarrantyClaim.objects.create(
                shop=request.user.shop, warranty=warranty,
                issue_description=issue,
                resolution=request.POST.get("resolution", ""),
                expected_return_date=expected,
                resolved_by=request.user,
            )
            warranty.status = W.Status.CLAIMED
            warranty.save(update_fields=["status"])
            # Per-unit return: flip only this warranty's physical unit, leaving
            # the rest of the purchased batch untouched.
            if warranty.product_unit_id:
                from catalog.models import ProductUnit
                ProductUnit.objects.filter(pk=warranty.product_unit_id).update(
                    status=ProductUnit.Status.RETURNED)
            # A warranty claim opens a repair ticket so it lands on the repair
            # tickets board, linked back to the warranty.
            create_ticket(
                shop=request.user.shop, customer=warranty.customer,
                device_description=warranty.product.name,
                complaint=issue or f"Warranty claim — {warranty.serial_no}",
                estimated_delivery=expected, warranty=warranty,
                created_by=request.user,
            )
            messages.success(request, "Warranty claim recorded — repair ticket opened.")
            return redirect("web:warranties")
        product = get_object_or_404(Product.objects, pk=_int_or_none(request.POST.get("product")))
        Warranty.objects.create(
            shop=request.user.shop, product=product,
            customer=Customer.objects.filter(pk=_int_or_none(request.POST.get("customer"))).first(),
            serial_no=request.POST.get("serial_no", ""),
            period_months=int(request.POST.get("period_months") or 12),
            start_date=_parse_date(request.POST.get("start_date")) or timezone.localdate(),
            terms=request.POST.get("terms", ""),
        )
        messages.success(request, "Warranty registered.")
        return redirect("web:warranties")

    from django.db.models import Count, Q

    from catalog.models import ProductUnit
    # Scan a unit barcode/serial to pull up its warranty for return/claim, with
    # expiry, buyer and supplier resolved from that one physical unit.
    barcode = (request.GET.get("barcode") or "").strip()
    scanned = None
    if barcode:
        # Resolve the scanned code, most-specific first:
        #  1. a per-unit barcode (ProductUnit)  → that exact unit's warranty
        #  2. a warranty serial                 → that exact coverage
        #  3. a product barcode / SKU           → list that product's sold
        #     warranties to pick the right customer from (plain-qty items have
        #     no per-unit serial to scan).
        unit = (ProductUnit.objects.filter(barcode=barcode)
                .select_related("product", "product__supplier").first())
        warranty = None
        product = unit.product if unit else None
        if unit is not None:
            warranty = (Warranty.objects.filter(product_unit=unit)
                        .select_related("customer", "product").order_by("-start_date").first())
        if warranty is None:
            warranty = (Warranty.objects.filter(serial_no=barcode)
                        .select_related("customer", "product", "product__supplier").first())
            if warranty is not None:
                product = warranty.product
        if product is None:
            product = (Product.objects.filter(barcode=barcode).first()
                       or Product.objects.filter(sku=barcode).first())

        # Single exact warranty → detailed card + eligibility.
        eligible, reason = False, ""
        if warranty is not None:
            warranty.live_status = warranty.compute_status()
            warranty.live_status_display = dict(Warranty.Status.choices).get(
                warranty.live_status, warranty.live_status)
            started = (warranty.sale_item_id is not None) or (
                unit is not None and unit.status == ProductUnit.Status.SOLD)
            if not (product and (product.warranty_months or 0) > 0) and not warranty.period_months:
                reason = "This product has no warranty coverage."
            elif warranty.status in (Warranty.Status.CLAIMED, Warranty.Status.VOID):
                reason = "Already claimed."
            elif not started:
                reason = "Only a sold unit can be claimed — this one is not sold yet."
            elif warranty.live_status == Warranty.Status.EXPIRED:
                reason = f"Warranty expired on {warranty.expiry_date}."
            else:
                eligible = True

        # Product barcode with no single warranty → list its sold coverages so
        # the owner can pick the right customer/unit to claim.
        matches = []
        if warranty is None and product is not None:
            mqs = (Warranty.objects.filter(product=product, sale_item__isnull=False)
                   .exclude(status__in=[Warranty.Status.CLAIMED, Warranty.Status.VOID])
                   .select_related("customer").order_by("-start_date")[:50])
            for w in mqs:
                w.live_status = w.compute_status()
                w.live_status_display = dict(Warranty.Status.choices).get(
                    w.live_status, w.live_status)
                w.claimable = w.live_status != Warranty.Status.EXPIRED
                matches.append(w)
            if not matches:
                reason = "Product found, but no sold warranty on record for it."

        scanned = {
            "barcode": barcode, "found": bool(unit or warranty or product),
            "unit": unit, "warranty": warranty, "product": product,
            "customer": getattr(warranty, "customer", None),
            "supplier": getattr(product, "supplier", None),
            "eligible": eligible, "reason": reason, "matches": matches,
        }

    # Active (sold) warranties — the coverage that started at sale, so the owner
    # can recognize sold-and-warrantied items and claim them. Paginated.
    active_qs = (
        Warranty.objects.filter(sale_item__isnull=False)
        .exclude(status__in=[Warranty.Status.CLAIMED, Warranty.Status.VOID])
        .select_related("product", "customer").order_by("expiry_date")
    )
    page = _paginate(request, active_qs)
    for w in page:
        w.live_status = w.compute_status()
        w.live_status_display = dict(Warranty.Status.choices).get(w.live_status, w.live_status)
        w.claimable = w.live_status != Warranty.Status.EXPIRED

    return render(request, "web/warranties.html", {
        "active": "warranties",
        "barcode": barcode, "scanned": scanned,
        "active_warranties": page, "page_obj": page,
        "return_policy": (request.user.shop.invoice_settings or {}).get("return_policy", ""),
    })


@shop_member_required
@perm_required("view_service")
def warranty_coverage(request):
    """Product-level overview: every active product with its supplier, selling
    price, warranty months and how many sold units are still in warranty (a
    warranty 'started' once it is bound to a sale line)."""
    from django.db.models import Count, Q

    today = timezone.localdate()
    started_active = Q(warranties__sale_item__isnull=False,
                       warranties__expiry_date__gte=today) & ~Q(
        warranties__status__in=[Warranty.Status.CLAIMED, Warranty.Status.VOID])
    products_qs = (
        Product.objects.filter(is_active=True).select_related("supplier")
        .annotate(covered_units=Count("warranties", filter=started_active))
        .order_by("name")
    )
    page = _paginate(request, products_qs)
    return render(request, "web/warranty_coverage.html", {
        "active": "warranty_coverage", "products_page": page, "page_obj": page,
    })


# --- Notifications ----------------------------------------------------------

@shop_member_required
def notifications(request):
    if request.method == "POST":
        Notification.objects.filter(is_read=False).update(is_read=True)
        return redirect("web:notifications")
    return render(request, "web/notifications.html", {
        "active": "notifications", "notifications": Notification.objects.all()[:100]})


# --- Users & Roles ----------------------------------------------------------

@shop_member_required
@perm_required("manage_users")
def users(request):
    shop = request.user.shop
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create_user":
            email = request.POST["email"].strip().lower()
            if User.objects.filter(email__iexact=email).exists():
                messages.error(request, "Email already exists.")
            else:
                role = request.POST.get("role", RoleType.CASHIER)
                # "Add custom role" chosen inline in the dropdown: create the
                # role on the fly, then assign the new user to it.
                if role == "__new__":
                    new_name = request.POST.get("role_new", "").strip()
                    if not new_name:
                        messages.error(request, "Enter a name for the new role.")
                        return redirect("web:users")
                    slug = _unique_role_slug(shop, new_name)
                    Role.objects.create(shop=shop, role_type=slug, name=new_name, is_system=False)
                    role = slug
                User.objects.create_user(
                    email=email, password=request.POST["password"],
                    shop=shop, role=role,
                    first_name=request.POST.get("first_name", ""),
                )
                messages.success(request, "User created.")
        elif action == "create_role":
            # Owner defines a custom role: a slug identifier + display name.
            # Permissions are assigned afterwards via the "set_perms" editor.
            name = request.POST.get("role_name", "").strip()
            if not name:
                messages.error(request, "Role name is required.")
            else:
                slug = _unique_role_slug(shop, name)
                role = Role.objects.create(shop=shop, role_type=slug, name=name, is_system=False)
                codes = request.POST.getlist("codes")
                if codes:
                    role.permissions.set(Permission.objects.filter(code__in=codes))
                messages.success(request, f"Role '{name}' created.")
        elif action == "delete_role":
            role = get_object_or_404(Role.objects.filter(shop_id=shop.id, is_system=False),
                                     pk=_int_or_none(request.POST.get("role_id")))
            if User.objects.filter(shop_id=shop.id, role=role.role_type).exists():
                messages.error(request, "Cannot delete: employees are assigned to this role.")
            else:
                role.delete()
                messages.success(request, "Role deleted.")
        elif action == "set_role":
            # Reassign an existing employee to another role.
            member = get_object_or_404(
                User.objects.filter(shop_id=shop.id).exclude(role=RoleType.OWNER),
                pk=_int_or_none(request.POST.get("user_id")),
            )
            member.role = request.POST.get("role", member.role)
            member.save(update_fields=["role"])
            messages.success(request, f"{member.email} role updated.")
        elif action == "set_perms":
            role = get_object_or_404(Role.objects.filter(shop_id=shop.id), pk=_int_or_none(request.POST.get("role_id")))
            codes = request.POST.getlist("codes")
            role.permissions.set(Permission.objects.filter(code__in=codes))
            from audit.models import AuditLog
            from audit.services import record
            record(action=AuditLog.Action.PERMISSION_CHANGE, actor=request.user,
                   shop=shop, target=role, description="Role perms updated")
            messages.success(request, "Permissions updated.")
        elif action == "reset_password":
            # Owner / manage_users can reset an employee's password. Owners are
            # excluded here (an owner's password is changed via settings, or by a
            # super-admin — feature #9).
            member = get_object_or_404(
                User.objects.filter(shop_id=shop.id).exclude(role=RoleType.OWNER),
                pk=_int_or_none(request.POST.get("user_id")),
            )
            new_pw = request.POST.get("new_password", "")
            if len(new_pw) < 6:
                messages.error(request, "Password must be at least 6 characters.")
            else:
                member.set_password(new_pw)
                member.save(update_fields=["password"])
                messages.success(request, f"Password reset for {member.email}.")
        elif action == "delete_user":
            member = get_object_or_404(
                User.objects.filter(shop_id=shop.id).exclude(role=RoleType.OWNER),
                pk=_int_or_none(request.POST.get("user_id")),
            )
            if member.id == request.user.id:
                messages.error(request, "You cannot delete your own account.")
            else:
                email = member.email
                member.delete()
                messages.success(request, f"Deleted user {email}.")
        return redirect("web:users")

    roles = list(
        Role.objects.filter(shop_id=shop.id).prefetch_related("permissions").order_by("-is_system", "name")
    )
    role_labels = {r.role_type: r.name for r in roles}
    return render(request, "web/users.html", {
        "active": "users",
        "users": User.objects.filter(shop_id=shop.id).order_by("email"),
        "roles": roles,
        "role_labels": role_labels,
        # Roles an employee can be assigned to (everything except Owner).
        "assignable_roles": [r for r in roles if r.role_type != RoleType.OWNER],
        "all_perms": Permission.objects.all(),
    })


# --- Settings & billing -----------------------------------------------------

@shop_member_required
def settings_view(request):
    shop = request.user.shop
    if request.method == "POST" and request.user.role == "owner":
        shop.name = request.POST.get("name", shop.name)
        shop.phone = request.POST.get("phone", "")
        shop.address = request.POST.get("address", "")
        shop.currency = request.POST.get("currency", shop.currency)
        shop.vat_enabled = request.POST.get("vat_enabled") == "on"
        shop.vat_percent = _dec(request.POST.get("vat_percent"))
        shop.vat_registration_no = request.POST.get("vat_registration_no", shop.vat_registration_no)
        if request.FILES.get("logo"):  # item 17: logo used on all invoices
            logo_err = _logo_error(request.FILES["logo"])
            if logo_err:
                messages.error(request, logo_err)
                return redirect("web:settings")
            shop.logo = request.FILES["logo"]
        # Invoice header/footer text shown on every printed invoice.
        settings_json = dict(shop.invoice_settings or {})
        settings_json["header_note"] = request.POST.get("invoice_header", "")
        settings_json["footer_note"] = request.POST.get("invoice_footer", "")
        settings_json["return_policy"] = request.POST.get("return_policy", "")
        shop.invoice_settings = settings_json
        shop.save()
        messages.success(request, "Settings saved.")
        return redirect("web:settings")
    from billing.services import subscription_status
    return render(request, "web/settings.html", {
        "active": "settings", "shop": shop,
        "subscription": subscription_status(shop),
    })
