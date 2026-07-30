"""Service-layer helpers for writing audit entries (keep views thin)."""
from .models import AuditLog


def record(
    *, action, actor=None, shop=None, impersonator=None,
    target=None, target_model="", target_id="", description="",
    changes=None, metadata=None,
):
    """
    Write one audit entry. ``target`` may be a model instance (auto-fills
    model label + pk). Never raises into the caller's happy path for logging
    failures would be worse than the missing log — but we let DB errors surface
    in tests by not swallowing here; callers wrap if needed.
    """
    if target is not None:
        target_model = target_model or target.__class__.__name__
        target_id = target_id or str(target.pk)
        if shop is None:
            shop = getattr(target, "shop", None)

    return AuditLog.objects.create(
        action=action,
        actor=actor,
        shop=shop,
        impersonator=impersonator,
        target_model=target_model,
        target_id=str(target_id),
        description=description,
        changes=changes or {},
        metadata=metadata or {},
    )
