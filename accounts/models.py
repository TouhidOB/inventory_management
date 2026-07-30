"""
Users and RBAC.

Design:
- ``Permission`` is a platform-global catalog of feature-level permission codes
  (e.g. ``view_profit``, ``delete_sale``, ``manage_users``).
- ``Role`` is tenant-scoped and maps a coarse role type (Owner / Manager / ...)
  to a set of permissions, editable per shop. Shops get seeded default roles.
- ``User`` carries a coarse ``role`` for fast checks and resolves fine-grained
  permissions through the shop's ``Role`` rows.

Platform staff (super admins) are ``is_staff`` / ``is_superuser`` users with no
shop; they operate the ``platform_admin`` panel, not a tenant.
"""
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models

from core.models import TimeStampedModel


class RoleType(models.TextChoices):
    OWNER = "owner", "Owner"
    MANAGER = "manager", "Manager"
    CASHIER = "cashier", "Cashier"
    INVENTORY_MANAGER = "inventory_manager", "Inventory Manager"
    ACCOUNTANT = "accountant", "Accountant"


class Permission(TimeStampedModel):
    """Global catalog of feature-level permissions. Not tenant-scoped."""

    code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=40, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["category", "code"]

    def __str__(self):
        return self.code


class Role(TimeStampedModel):
    """
    A per-shop role mapping a RoleType to a set of permissions. ``is_system``
    marks the auto-seeded defaults so the UI can protect them from deletion.
    """

    shop = models.ForeignKey("tenants.Shop", on_delete=models.CASCADE, related_name="roles")
    role_type = models.CharField(max_length=30, choices=RoleType.choices)
    name = models.CharField(max_length=80, blank=True)
    permissions = models.ManyToManyField(Permission, blank=True, related_name="roles")
    is_system = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["shop", "role_type"], name="uniq_role_type_per_shop"
            ),
        ]

    def __str__(self):
        return f"{self.shop_id}:{self.role_type}"


class UserManager(BaseUserManager):
    """Email-based manager for the custom user model."""

    use_in_migrations = True

    def _create_user(self, email, password, **extra):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        if extra.get("is_staff") is not True or extra.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_staff and is_superuser = True")
        return self._create_user(email, password, **extra)


class User(AbstractUser):
    """
    Custom user, email login. ``shop`` is null for platform staff.

    ``role`` is the coarse role type; fine permissions resolve via the shop's
    Role rows. Owners implicitly have every permission.
    """

    username = None  # email is the identifier
    email = models.EmailField(unique=True)

    shop = models.ForeignKey(
        "tenants.Shop", on_delete=models.CASCADE,
        related_name="users", null=True, blank=True,
    )
    branch = models.ForeignKey(
        "tenants.Branch", on_delete=models.SET_NULL,
        related_name="users", null=True, blank=True,
    )
    role = models.CharField(
        max_length=30, choices=RoleType.choices,
        default=RoleType.OWNER, blank=True,
    )
    phone = models.CharField(max_length=30, blank=True)
    # Last request timestamp; drives the "online now" green dot in the super-admin
    # active-users table. Bumped (throttled) by LastSeenMiddleware.
    last_seen = models.DateTimeField(null=True, blank=True, editable=False)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return self.email

    # A user is considered "online" if seen within this window.
    ONLINE_WINDOW_SECONDS = 300

    @property
    def is_online(self) -> bool:
        if not self.last_seen:
            return False
        from django.utils import timezone
        return (timezone.now() - self.last_seen).total_seconds() <= self.ONLINE_WINDOW_SECONDS

    @property
    def is_platform_staff(self) -> bool:
        return self.is_staff and self.shop_id is None

    def has_perm_code(self, code: str) -> bool:
        """
        Resolve a feature-level permission for this user within their shop.

        - Platform staff and shop owners: always allowed.
        - Others: allowed if their shop Role (matching ``role``) grants ``code``.
        """
        if self.is_platform_staff or self.is_superuser:
            return True
        if self.role == RoleType.OWNER:
            return True
        if self.shop_id is None:
            return False
        return Role.objects.filter(
            shop_id=self.shop_id, role_type=self.role,
            permissions__code=code,
        ).exists()
