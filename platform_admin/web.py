"""
Server-rendered Super Admin dashboard (session auth, staff only).

Cross-shop reads run inside ``bypass_tenant_scope`` because platform staff
legitimately operate across every tenant. "Login as shop" swaps the request
user to that shop's owner for the session (so the normal, fully tenant-scoped
shop frontend just works) while remembering the admin to switch back. Both the
start and end are written to the audit log.
"""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import login
from django.http import HttpResponse
from django.db.models import Count, DecimalField, Max, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.models import RoleType, User
from audit.models import AuditLog
from audit.services import record
from billing.models import ManualPayment
from billing.services import approve_payment, reject_payment
from core.tenant_context import bypass_tenant_scope
from public_api.models import APIKey
from tenants.models import Shop, Subscription, SubscriptionPlan

from django.core.paginator import Paginator

from .models import ContactMessage, TutorialVideo


def _page(request, qs, per_page=15):
    return Paginator(qs, per_page).get_page(request.GET.get("page"))
from tenants.services import register_shop

from web.guards import platform_staff_required

IMPERSONATOR_KEY = "impersonator_id"
_DEC = DecimalField(max_digits=18, decimal_places=2)


@platform_staff_required
def dashboard(request):
    now = timezone.now()
    with bypass_tenant_scope():
        shops = Shop.objects.all()
        total = shops.count()
        active = shops.filter(is_active=True).count()
        on_trial = shops.filter(trial_ends_at__gt=now).count()
        by_type = list(shops.values("business_type").annotate(n=Count("id")).order_by("-n"))
        pending_payments = ManualPayment.objects.filter(status=ManualPayment.Status.PENDING).count()
        approved_revenue = ManualPayment.objects.filter(
            status=ManualPayment.Status.APPROVED
        ).aggregate(v=Coalesce(Sum("amount", output_field=_DEC), Decimal("0"), output_field=_DEC))["v"]
        recent_shops = list(shops.select_related("plan").order_by("-created_at")[:8])
    unread_messages = ContactMessage.objects.filter(is_read=False).count()
    return render(request, "platform/dashboard.html", {
        "active": "dashboard",
        "total": total, "active_shops": active, "trial": on_trial, "suspended": total - active,
        "by_type": by_type, "pending_payments": pending_payments,
        "approved_revenue": approved_revenue, "recent_shops": recent_shops,
        "unread_messages": unread_messages,
    })


@platform_staff_required
def shops(request):
    q = request.GET.get("q", "")
    with bypass_tenant_scope():
        qs = Shop.objects.select_related("plan").annotate(user_count=Count("users")).order_by("-created_at")
        if q:
            qs = qs.filter(name__icontains=q)
        page = _page(request, qs)
        rows = list(page)
        now = timezone.now()
        for s in rows:
            if not s.is_active and s.suspended_at:
                s.days_suspended = (now - s.suspended_at).days
                s.can_delete = s.days_suspended >= SHOP_DELETE_COOLOFF.days
            else:
                s.days_suspended = 0
                s.can_delete = False
    return render(request, "platform/shops.html", {
        "active": "shops", "shops": rows, "page_obj": page, "q": q,
        "cooloff_days": SHOP_DELETE_COOLOFF.days,
    })


@platform_staff_required
def shop_create(request):
    """Super Admin provisions a brand-new shop + its owner account."""
    if request.method == "POST":
        email = request.POST.get("owner_email", "").strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            messages.error(request, "A user with that email already exists.")
            return redirect("platform:shop_create")
        plan = SubscriptionPlan.objects.filter(pk=request.POST.get("plan")).first()
        shop, owner = register_shop(
            name=request.POST["name"],
            owner_email=email,
            owner_password=request.POST["owner_password"],
            owner_name=request.POST.get("owner_name", ""),
            business_type=request.POST.get("business_type", Shop.BusinessType.GENERAL),
            phone=request.POST.get("phone", ""),
            plan=plan,
        )
        record(action=AuditLog.Action.CREATE, actor=request.user, shop=shop, target=shop,
               description=f"Shop '{shop.name}' created by platform admin")
        messages.success(request, f"Shop '{shop.name}' created with owner {owner.email}.")
        return redirect("platform:shops")
    return render(request, "platform/shop_create.html", {
        "active": "shops",
        "plans": SubscriptionPlan.objects.filter(is_active=True),
        "business_types": Shop.BusinessType.choices,
    })


@platform_staff_required
@require_http_methods(["POST"])
def shop_toggle(request, pk):
    with bypass_tenant_scope():
        shop = get_object_or_404(Shop.objects, pk=pk)
        shop.is_active = not shop.is_active
        # Stamp when suspended so we can enforce a 15-day cool-off before delete;
        # clear it on re-activation.
        shop.suspended_at = None if shop.is_active else timezone.now()
        shop.save(update_fields=["is_active", "suspended_at"])
    record(action=AuditLog.Action.SUSPEND if not shop.is_active else AuditLog.Action.ACTIVATE,
           actor=request.user, shop=shop, target=shop,
           description=f"Shop {'suspended' if not shop.is_active else 'activated'} by platform admin")
    messages.success(request, f"{shop.name} {'suspended' if not shop.is_active else 'activated'}.")
    return redirect("platform:shops")


@platform_staff_required
@require_http_methods(["POST"])
def shop_owner_password(request, pk):
    """Super-admin resets a shop owner's password (feature #9)."""
    with bypass_tenant_scope():
        shop = get_object_or_404(Shop.objects, pk=pk)
        owner = User.objects.filter(shop_id=shop.id, role=RoleType.OWNER).first()
        if owner is None:
            messages.error(request, "This shop has no owner account.")
            return redirect("platform:shops")
        new_pw = request.POST.get("new_password", "")
        if len(new_pw) < 6:
            messages.error(request, "Password must be at least 6 characters.")
            return redirect("platform:shops")
        owner.set_password(new_pw)
        owner.save(update_fields=["password"])
    record(action=AuditLog.Action.UPDATE, actor=request.user, shop=shop, target=owner,
           description=f"Owner password reset by platform admin ({owner.email})")
    messages.success(request, f"Owner password reset for {shop.name} ({owner.email}).")
    return redirect("platform:shops")


@platform_staff_required
@require_http_methods(["POST"])
def impersonate(request, pk):
    """Log in as the shop's owner for this session; remember the admin."""
    with bypass_tenant_scope():
        shop = get_object_or_404(Shop.objects, pk=pk)
        owner = User.objects.filter(shop_id=shop.id, role=RoleType.OWNER, is_active=True).first()
    if owner is None:
        messages.error(request, "This shop has no active owner to impersonate.")
        return redirect("platform:shops")

    admin_id = request.user.id
    record(action=AuditLog.Action.IMPERSONATE_START, actor=request.user, shop=shop, target=shop,
           description=f"Platform admin impersonating '{shop.name}'",
           metadata={"impersonator_id": admin_id, "owner_id": owner.id})
    login(request, owner)  # rotates session; set marker AFTER
    request.session[IMPERSONATOR_KEY] = admin_id
    messages.info(request, f"You are now working inside {shop.name}.")
    return redirect("web:dashboard")


@require_http_methods(["POST"])
def stop_impersonation(request):
    """Switch back to the platform admin. Available while impersonating."""
    admin_id = request.session.get(IMPERSONATOR_KEY)
    if not admin_id:
        return redirect("web:dashboard")
    admin = User.objects.filter(pk=admin_id, is_staff=True).first()
    record(action=AuditLog.Action.IMPERSONATE_END, actor=admin,
           description="Platform admin stopped impersonation",
           metadata={"was_user": request.user.id})
    if admin is not None:
        login(request, admin)  # rotates session, drops the marker
    return redirect("platform:dashboard")


@platform_staff_required
def active_users(request):
    """Cross-shop table of user accounts with a live 'online now' indicator.

    Online = ``last_seen`` within ``User.ONLINE_WINDOW_SECONDS`` (green dot),
    kept fresh by ``accounts.middleware.LastSeenMiddleware``. Defaults to
    showing only currently-online users; ``?all=1`` lists everyone.
    """
    show_all = request.GET.get("all") == "1"
    q = request.GET.get("q", "")
    cutoff = timezone.now() - timedelta(seconds=User.ONLINE_WINDOW_SECONDS)
    with bypass_tenant_scope():
        qs = User.objects.select_related("shop").order_by("-last_seen")
        if q:
            qs = qs.filter(email__icontains=q)
        if not show_all:
            qs = qs.filter(last_seen__gte=cutoff)
        online_count = User.objects.filter(last_seen__gte=cutoff).count()
        page = _page(request, qs, per_page=25)
        rows = list(page)
    return render(request, "platform/users.html", {
        "active": "users", "users": rows, "page_obj": page,
        "q": q, "show_all": show_all, "online_count": online_count,
    })


@platform_staff_required
def payments(request):
    with bypass_tenant_scope():
        page = _page(request, ManualPayment.objects.select_related("shop", "invoice").order_by("-submitted_at"))
    return render(request, "platform/payments.html", {"active": "payments", "payments": page, "page_obj": page})


@platform_staff_required
@require_http_methods(["POST"])
def payment_review(request, pk):
    with bypass_tenant_scope():
        payment = get_object_or_404(ManualPayment.objects, pk=pk)
    decision = request.POST.get("decision")
    try:
        if decision == "approve":
            approve_payment(payment=payment, reviewer=request.user)
            messages.success(request, "Payment approved; subscription extended.")
        else:
            reject_payment(payment=payment, reviewer=request.user,
                           reason=request.POST.get("reason", "Not verified"))
            messages.success(request, "Payment rejected.")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("platform:payments")


@platform_staff_required
def api_keys(request):
    if request.method == "POST":
        with bypass_tenant_scope():
            shop = get_object_or_404(Shop.objects, pk=request.POST["shop"])
        instance, raw = APIKey.generate(
            shop=shop, name=request.POST.get("name", "API key"),
            can_read=request.POST.get("can_read") == "on",
            can_write=request.POST.get("can_write") == "on",
            resources=request.POST.getlist("resources"),
        )
        record(action=AuditLog.Action.CREATE, actor=request.user, shop=shop, target=instance,
               description="Issued public API key")
        # Stash the raw key (shown once) so the redirect target can display it
        # in a copy box. Hashes are one-way — this is the only chance to copy it.
        request.session["new_api_key"] = raw
        request.session["new_api_key_name"] = instance.name
        messages.success(request, "Key created — copy it now, it won't be shown again.")
        return redirect("platform:api_keys")
    with bypass_tenant_scope():
        page = _page(request, APIKey.objects.select_related("shop").order_by("-created_at"))
        keys = list(page)
        shop_list = list(Shop.objects.order_by("name"))
    return render(request, "platform/api_keys.html", {
        "active": "api_keys", "keys": keys, "page_obj": page, "shops": shop_list,
        "resource_options": ["products", "inventory", "customers", "sales", "reports"],
        "new_api_key": request.session.pop("new_api_key", None),
        "new_api_key_name": request.session.pop("new_api_key_name", ""),
    })


# A shop must sit suspended this long before it can be permanently deleted.
SHOP_DELETE_COOLOFF = timedelta(days=15)


@platform_staff_required
@require_http_methods(["POST"])
def shop_delete(request, pk):
    """Permanently delete a shop and ALL its data (cascades). Requires the
    admin to re-type the shop name as confirmation."""
    with bypass_tenant_scope():
        shop = get_object_or_404(Shop.objects, pk=pk)
        typed = request.POST.get("confirm_name", "").strip()
        if typed != shop.name:
            messages.error(request, "Confirmation name did not match — shop not deleted.")
            return redirect("platform:shops")
        # Guard: only a shop suspended for at least 15 days can be deleted.
        if shop.is_active:
            messages.error(request, "Suspend the shop first — it must be suspended for 15 days before deletion.")
            return redirect("platform:shops")
        elapsed = (timezone.now() - shop.suspended_at) if shop.suspended_at else timedelta(0)
        if elapsed < SHOP_DELETE_COOLOFF:
            left = SHOP_DELETE_COOLOFF.days - elapsed.days
            messages.error(request, f"Shop must be suspended for 15 days before deletion — {left} day(s) left.")
            return redirect("platform:shops")
        name, sid = shop.name, shop.pk
        # Log at platform level (shop=None) so the record survives the cascade.
        record(action=AuditLog.Action.DELETE, actor=request.user, shop=None,
               target_model="Shop", target_id=sid,
               description=f"Permanently deleted shop '{name}' (#{sid}) and all its data")
        shop.delete()
    messages.success(request, f"Shop '{name}' permanently deleted.")
    return redirect("platform:shops")


@platform_staff_required
@require_http_methods(["POST"])
def api_key_regenerate(request, pk):
    """Issue a fresh secret for an existing key and show it in the copy box."""
    with bypass_tenant_scope():
        key = get_object_or_404(APIKey.objects, pk=pk)
        raw = key.rotate()
    record(action=AuditLog.Action.UPDATE, actor=request.user, shop=key.shop, target=key,
           description="Regenerated public API key")
    request.session["new_api_key"] = raw
    request.session["new_api_key_name"] = key.name
    messages.success(request, "Key regenerated — copy it now, it won't be shown again.")
    return redirect("platform:api_keys")


@platform_staff_required
@require_http_methods(["POST"])
def api_key_revoke(request, pk):
    with bypass_tenant_scope():
        key = get_object_or_404(APIKey.objects, pk=pk)
        key.is_active = False
        key.save(update_fields=["is_active"])
    record(action=AuditLog.Action.UPDATE, actor=request.user, shop=key.shop, target=key,
           description="Revoked public API key")
    messages.success(request, "Key revoked.")
    return redirect("platform:api_keys")


@platform_staff_required
def messages_inbox(request):
    return render(request, "platform/messages.html", {
        "active": "messages",
        "messages_list": _page(request, ContactMessage.objects.all()),
        "page_obj": _page(request, ContactMessage.objects.all()),
    })


@platform_staff_required
@require_http_methods(["POST"])
def message_read(request, pk):
    msg = get_object_or_404(ContactMessage, pk=pk)
    msg.is_read = True
    msg.save(update_fields=["is_read"])
    return redirect("platform:messages")


@platform_staff_required
@require_http_methods(["POST"])
def message_delete(request, pk):
    """Only platform staff can delete — and only makes sense after reading."""
    msg = get_object_or_404(ContactMessage, pk=pk)
    msg.delete()
    messages.success(request, "Message deleted.")
    return redirect("platform:messages")


# --- Tutorial videos (platform-wide help content shown on every dashboard) ---

@platform_staff_required
def tutorials(request):
    """List + add tutorial videos (super-admin only)."""
    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        url = request.POST.get("youtube_url", "").strip()
        seq = request.POST.get("sequence", "").strip()
        if not title or not url:
            messages.error(request, "Title and YouTube link are required.")
            return redirect("platform:tutorials")
        try:
            sequence = int(seq) if seq else (
                (TutorialVideo.objects.aggregate(m=Max("sequence"))["m"] or 0) + 1
            )
        except ValueError:
            messages.error(request, "Sequence must be a number.")
            return redirect("platform:tutorials")
        video = TutorialVideo(title=title[:200], youtube_url=url, sequence=max(1, sequence))
        if not video.video_id:
            messages.error(request, "That doesn't look like a valid YouTube link.")
            return redirect("platform:tutorials")
        video.is_active = request.POST.get("is_active", "on") == "on"
        video.save()
        messages.success(request, f"Tutorial “{video.title}” added.")
        return redirect("platform:tutorials")
    return render(request, "platform/tutorials.html", {
        "active": "tutorials",
        "videos": TutorialVideo.objects.all(),
    })


@platform_staff_required
@require_http_methods(["POST"])
def tutorial_edit(request, pk):
    video = get_object_or_404(TutorialVideo, pk=pk)
    title = request.POST.get("title", "").strip()
    url = request.POST.get("youtube_url", "").strip()
    if not title or not url:
        messages.error(request, "Title and YouTube link are required.")
        return redirect("platform:tutorials")
    video.title = title[:200]
    video.youtube_url = url
    try:
        video.sequence = max(1, int(request.POST.get("sequence") or video.sequence))
    except ValueError:
        pass
    if not video.video_id:
        messages.error(request, "That doesn't look like a valid YouTube link.")
        return redirect("platform:tutorials")
    video.is_active = request.POST.get("is_active") == "on"
    video.save()
    messages.success(request, "Tutorial updated.")
    return redirect("platform:tutorials")


@platform_staff_required
@require_http_methods(["POST"])
def tutorial_delete(request, pk):
    video = get_object_or_404(TutorialVideo, pk=pk)
    video.delete()
    messages.success(request, "Tutorial deleted.")
    return redirect("platform:tutorials")


FEATURE_KEYS = [
    "pos", "basic_analytics", "advanced_analytics", "reports_export",
    "multi_branch", "api_access",
]


@platform_staff_required
def plans(request):
    """Manage the single subscription plan + its feature flags."""
    plan = SubscriptionPlan.objects.order_by("-is_active", "price_monthly").first()
    if request.method == "POST":
        if plan and request.POST.get("action") == "update_plan":
            plan.name = request.POST.get("name", plan.name)
            plan.price_monthly = request.POST.get("price_monthly") or 0
            plan.price_yearly = request.POST.get("price_yearly") or 0
            plan.max_users = request.POST.get("max_users") or plan.max_users
            plan.max_branches = request.POST.get("max_branches") or plan.max_branches
            plan.max_products = request.POST.get("max_products") or plan.max_products
            enabled = set(request.POST.getlist("features"))
            plan.features = {k: (k in enabled) for k in FEATURE_KEYS}
            plan.is_active = True
            plan.save()
            messages.success(request, f"{plan.name} updated.")
        return redirect("platform:plans")

    if plan:
        plan.features_enabled = [k for k, v in (plan.features or {}).items() if v]
    return render(request, "platform/plans.html", {
        "active": "plans",
        "plan": plan,
        "feature_keys": FEATURE_KEYS,
    })
