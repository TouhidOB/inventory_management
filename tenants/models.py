"""Tenant root models: subscription plans, shops (tenants), branches."""
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from core.models import TimeStampedModel


class SubscriptionPlan(TimeStampedModel):
    """A billing tier. Not tenant-scoped — plans are platform-global."""

    class Tier(models.TextChoices):
        FREE = "free", "Free"
        BASIC = "basic", "Basic"
        PROFESSIONAL = "professional", "Professional"
        ENTERPRISE = "enterprise", "Enterprise"

    name = models.CharField(max_length=80)
    tier = models.CharField(max_length=20, choices=Tier.choices, unique=True)
    price_monthly = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    price_yearly = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Feature gating. Stored as JSON so plans are editable without migrations.
    # Keys are feature flags checked by the FeatureGate (e.g. "multi_branch",
    # "advanced_analytics", "api_access").
    features = models.JSONField(default=dict, blank=True)

    # Hard limits enforced on top of feature flags.
    max_users = models.PositiveIntegerField(default=2)
    max_branches = models.PositiveIntegerField(default=1)
    max_products = models.PositiveIntegerField(default=100)

    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["price_monthly"]

    def __str__(self):
        return f"{self.name} ({self.get_tier_display()})"

    def has_feature(self, flag: str) -> bool:
        return bool(self.features.get(flag, False))


class Shop(TimeStampedModel):
    """
    The tenant. Every tenant-owned row points here via ``shop``.

    NOT a TenantScopedModel itself — it is the scope root.
    """

    class BusinessType(models.TextChoices):
        ELECTRONICS = "electronics", "Electronics"
        COMPUTER = "computer", "Computer"
        MOBILE = "mobile", "Mobile & Accessories"
        GENERAL = "general", "General Retail"
        OTHER = "other", "Other"

    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=160, unique=True, blank=True)
    subdomain = models.CharField(
        max_length=63, unique=True, blank=True, null=True,
        help_text="Optional host-based routing key, e.g. 'acme' -> acme.stockwhisk.app",
    )
    logo = models.ImageField(upload_to="shop_logos/", blank=True, null=True)

    business_type = models.CharField(
        max_length=20, choices=BusinessType.choices, default=BusinessType.GENERAL
    )
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)

    # Locale / financial config
    currency = models.CharField(max_length=3, default="BDT")
    vat_enabled = models.BooleanField(default=False)
    vat_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    vat_registration_no = models.CharField(max_length=50, blank=True)

    # Invoice presentation settings (prefix, footer note, etc.)
    invoice_settings = models.JSONField(default=dict, blank=True)
    opening_hours = models.JSONField(default=dict, blank=True)

    # Subscription state (denormalized current plan for fast gating).
    plan = models.ForeignKey(
        SubscriptionPlan, on_delete=models.PROTECT,
        related_name="shops", null=True, blank=True,
    )
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    # When the shop was last suspended (is_active flipped to False). Used to
    # enforce a cool-off before a shop can be permanently deleted.
    suspended_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name) or "shop"
            slug = base
            i = 1
            while Shop.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                i += 1
                slug = f"{base}-{i}"
            self.slug = slug
        super().save(*args, **kwargs)

    @property
    def on_trial(self) -> bool:
        return bool(self.trial_ends_at and self.trial_ends_at > timezone.now())

    def has_feature(self, flag: str) -> bool:
        """Central feature check used by the FeatureGate permission/decorator."""
        if not self.is_active:
            return False
        if self.plan is None:
            return False
        return self.plan.has_feature(flag)


class Branch(TimeStampedModel):
    """
    A physical location within a shop. Built now (per spec) even though the
    multi-branch *feature* is gated to a later phase, so future migrations are
    painless. Carries its own explicit ``shop`` FK.
    """

    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name="branches")
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=20, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)
    is_main = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "branches"
        constraints = [
            models.UniqueConstraint(
                fields=["shop", "name"], name="uniq_branch_name_per_shop"
            ),
        ]

    def __str__(self):
        return f"{self.shop.name} — {self.name}"


class Subscription(TimeStampedModel):
    """
    Billing subscription lifecycle for a shop. One active row per shop;
    history retained for audit. Payment integration lands in the billing app
    (Phase 1/2) — this holds state only.
    """

    class Status(models.TextChoices):
        TRIALING = "trial", "Trial"
        ACTIVE = "active", "Active"
        GRACE_PERIOD = "grace_period", "Grace period"
        PAST_DUE = "past_due", "Past due"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired"

    class Cycle(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        YEARLY = "yearly", "Yearly"

    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name="subscriptions")
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.TRIALING)
    cycle = models.CharField(max_length=10, choices=Cycle.choices, default=Cycle.MONTHLY)
    started_at = models.DateTimeField(default=timezone.now)
    current_period_start = models.DateTimeField(default=timezone.now)
    current_period_end = models.DateTimeField(null=True, blank=True)
    # Informational only — nothing charges automatically.
    auto_renew = models.BooleanField(default=False)
    is_current = models.BooleanField(default=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.shop.name} · {self.plan.tier} · {self.status}"

    @property
    def is_live(self) -> bool:
        if self.status in {self.Status.CANCELLED, self.Status.EXPIRED}:
            return False
        if self.current_period_end and self.current_period_end < timezone.now():
            return False
        return True
