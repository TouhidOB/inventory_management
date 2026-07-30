"""
Subscription feature gating — the single reusable place plan restrictions live.

Use as a DRF permission (``FeatureRequired.as_permission("multi_branch")``) or
as a plain guard (``require_feature(shop, "api_access")``) in service code.
Never scatter ``if shop.plan.tier == ...`` checks through the codebase.
"""
from rest_framework.permissions import BasePermission

from .exceptions import FeatureNotAvailable


def require_feature(shop, flag: str):
    """Raise FeatureNotAvailable if ``shop`` lacks ``flag``. Returns True else."""
    if shop is None or not shop.has_feature(flag):
        raise FeatureNotAvailable(flag)
    return True


class FeatureRequired(BasePermission):
    """
    DRF permission that gates a view behind a plan feature flag.

    Subclass via ``FeatureRequired.as_permission("advanced_analytics")`` or set
    ``feature_flag`` on the view.
    """

    feature_flag: str | None = None
    message = "Your subscription plan does not include this feature."

    @classmethod
    def as_permission(cls, flag: str):
        return type(f"FeatureRequired_{flag}", (cls,), {"feature_flag": flag})

    def has_permission(self, request, view):
        flag = getattr(view, "feature_flag", None) or self.feature_flag
        if flag is None:
            return True
        shop = getattr(request, "tenant", None)
        return bool(shop and shop.has_feature(flag))
