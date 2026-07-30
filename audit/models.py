"""
Generic append-only audit log used across every app.

Records who did what, when, to which object, optionally with a before/after
diff. Mandatory for: invoice deletion, price changes, stock adjustments, user
permission changes, and impersonation sessions.

``shop`` is nullable because platform-level actions (impersonation start, shop
suspension) have no single tenant scope from the actor's side.
"""
from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class AuditLog(TimeStampedModel):
    class Action(models.TextChoices):
        CREATE = "create", "Create"
        UPDATE = "update", "Update"
        DELETE = "delete", "Delete"
        LOGIN = "login", "Login"
        LOGOUT = "logout", "Logout"
        IMPERSONATE_START = "impersonate_start", "Impersonation started"
        IMPERSONATE_END = "impersonate_end", "Impersonation ended"
        PERMISSION_CHANGE = "permission_change", "Permission change"
        PRICE_CHANGE = "price_change", "Price change"
        STOCK_ADJUST = "stock_adjust", "Stock adjustment"
        SUSPEND = "suspend", "Suspend"
        ACTIVATE = "activate", "Activate"

    shop = models.ForeignKey(
        "tenants.Shop", on_delete=models.CASCADE,
        related_name="audit_logs", null=True, blank=True, db_index=True,
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        related_name="audit_actions", null=True, blank=True,
    )
    # Set when the action was performed under an impersonation session.
    impersonator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        related_name="impersonated_actions", null=True, blank=True,
    )

    action = models.CharField(max_length=30, choices=Action.choices, db_index=True)
    target_model = models.CharField(max_length=100, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    description = models.CharField(max_length=255, blank=True)
    changes = models.JSONField(default=dict, blank=True)  # {field: [old, new]}
    metadata = models.JSONField(default=dict, blank=True)  # ip, user agent, etc.

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["shop", "action", "created_at"]),
            models.Index(fields=["target_model", "target_id"]),
        ]

    def __str__(self):
        who = self.actor_id or "system"
        return f"[{self.action}] by {who} on {self.target_model}#{self.target_id}"
