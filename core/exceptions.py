"""Custom exceptions shared across apps."""


class StockWhiskError(Exception):
    """Base class for all domain errors."""


class TenantResolutionError(StockWhiskError):
    """Raised when the current tenant cannot be determined for a request."""


class FeatureNotAvailable(StockWhiskError):
    """Raised when a shop's subscription plan does not include a feature."""

    def __init__(self, feature: str, message: str | None = None):
        self.feature = feature
        super().__init__(message or f"Feature '{feature}' is not in your current plan.")


class PermissionDenied(StockWhiskError):
    """Raised when a user's role lacks a required permission."""
