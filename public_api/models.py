"""
Public API keys for the Enterprise tier (9.7).

Keys are hashed at rest (only a short prefix is stored in clear for lookup and
display). The raw key is shown exactly once at creation. The tenant is always
resolved FROM the key — never from client input — so a key can only ever touch
its own shop's data.
"""
import hashlib
import secrets

from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel

KEY_PREFIX = "sk_live_"


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class APIKey(TimeStampedModel):
    """Not TenantScopedModel: platform staff manage these across shops, and the
    tenant binding is the whole point — stored explicitly, never auto-filtered."""

    class RateTier(models.TextChoices):
        STANDARD = "standard", "Standard"
        ENTERPRISE = "enterprise", "Enterprise"

    shop = models.ForeignKey("tenants.Shop", on_delete=models.CASCADE, related_name="api_keys")
    name = models.CharField(max_length=100)
    prefix = models.CharField(max_length=16, db_index=True)  # first chars, for lookup
    key_hash = models.CharField(max_length=64, unique=True)

    # Scopes: what this key may do.
    can_read = models.BooleanField(default=True)
    can_write = models.BooleanField(default=False)
    resources = models.JSONField(default=list, blank=True)  # e.g. ["products","sales"]

    rate_tier = models.CharField(max_length=15, choices=RateTier.choices, default=RateTier.STANDARD)
    is_active = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.prefix}…) for shop#{self.shop_id}"

    @classmethod
    def generate(cls, *, shop, name, can_read=True, can_write=False, resources=None,
                 rate_tier=RateTier.STANDARD):
        """Create a key, returning (instance, raw_key). Raw shown only once."""
        raw = KEY_PREFIX + secrets.token_urlsafe(32)
        instance = cls.objects.create(
            shop=shop, name=name, prefix=raw[:12], key_hash=hash_key(raw),
            can_read=can_read, can_write=can_write, resources=resources or [],
            rate_tier=rate_tier,
        )
        return instance, raw

    def rotate(self):
        """Issue a fresh secret for this key (same scopes/shop). Invalidates the
        old value and returns the new raw key — shown once. Lets an admin obtain
        a copyable key again from the dashboard without creating a new row."""
        raw = KEY_PREFIX + secrets.token_urlsafe(32)
        self.prefix = raw[:12]
        self.key_hash = hash_key(raw)
        self.is_active = True
        self.save(update_fields=["prefix", "key_hash", "is_active"])
        return raw

    def allows(self, resource, write=False):
        if not self.is_active:
            return False
        if resource not in self.resources:
            return False
        if write and not self.can_write:
            return False
        return self.can_read or write

    def touch(self):
        self.last_used_at = timezone.now()
        self.save(update_fields=["last_used_at"])
