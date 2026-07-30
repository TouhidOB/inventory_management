"""Template context: current shop, the user's effective permissions, alerts."""
from accounts.models import Permission, Role


def shop_context(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or user.shop_id is None:
        return {}

    if user.role == "owner":
        perms = set(Permission.objects.values_list("code", flat=True))
    else:
        perms = set(
            Role.objects.filter(shop_id=user.shop_id, role_type=user.role)
            .values_list("permissions__code", flat=True)
        )
        perms.discard(None)

    from notifications.models import Notification

    unread = Notification.objects.filter(is_read=False).count()
    return {
        "current_shop": user.shop,
        "perms_set": perms,
        "is_owner": user.role == "owner",
        "unread_count": unread,
    }
