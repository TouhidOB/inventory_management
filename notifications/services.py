"""Notification dispatch service — the single entry point used across apps."""
from accounts.models import RoleType, User

from . import channels, whatsapp
from .models import Notification, NotificationType, ShopAlertConfig


def get_alert_config(shop):
    cfg, _ = ShopAlertConfig.all_objects.get_or_create(shop_id=shop.id, defaults={"shop_id": shop.id})
    return cfg


def alert_low_stock_realtime(*, shop, products):
    """
    Raise an immediate low/out-of-stock notification for the given products.

    Called right after a sale (via ``transaction.on_commit``). No-op unless the
    shop's alert config is enabled AND in REALTIME mode — DAILY shops rely on
    the ``scan_low_stock`` Celery digest instead. De-dupes so a product with an
    existing unread low-stock notice isn't alerted again.
    """
    cfg = get_alert_config(shop)
    if not cfg.low_stock_enabled or cfg.mode != ShopAlertConfig.Mode.REALTIME:
        return

    for product in products:
        # ``current_stock`` was refreshed on the instance by recalc_stock.
        if not product.is_low_stock:
            continue
        already = Notification.all_objects.filter(
            shop_id=shop.id, is_read=False, metadata__product_id=product.id,
        ).exists()
        if already:
            continue
        out = product.current_stock <= 0
        notify(
            shop=shop,
            type=NotificationType.OUT_OF_STOCK if out else NotificationType.LOW_STOCK,
            title=f"{'Out of stock' if out else 'Low stock'}: {product.name}",
            message=(
                f"{product.name} is out of stock." if out
                else f"{product.name} is low: {product.current_stock}/{product.reorder_level} left."
            ),
            metadata={"product_id": product.id, "current_stock": str(product.current_stock)},
            email=cfg.email_enabled, sms=cfg.sms_enabled, whatsapp=cfg.whatsapp_enabled,
        )


def notify(
    *, shop, type, title, message="", user=None, metadata=None,
    email=False, sms=False, whatsapp=False, recipients=None,
):
    """
    Create an in-app notification and optionally fan out to other channels.

    ``recipients``: explicit list of User objects for email/sms/whatsapp. If
    omitted, defaults to the shop's owners.
    """
    note = Notification.all_objects.create(
        shop_id=shop.id, user=user, type=type, title=title,
        message=message, metadata=metadata or {},
    )
    sent = ["in_app"]

    if recipients is None:
        recipients = list(
            User._default_manager.filter(
                shop_id=shop.id, role=RoleType.OWNER, is_active=True
            )
        )

    for r in recipients:
        if email and channels.send_email(r.email, title, message):
            sent.append("email")
        if sms and channels.send_sms(getattr(r, "phone", ""), message):
            sent.append("sms")
        if whatsapp and channels.send_whatsapp(getattr(r, "phone", ""), message):
            sent.append("whatsapp")

    note.sent_channels = sorted(set(sent))
    note.save(update_fields=["sent_channels"])
    return note


def notify_customer_whatsapp(*, shop, customer, template_key, params=None):
    """
    Send an approved WhatsApp template to a customer, honoring opt-in consent.
    Used by service-ticket / warranty / invoice flows. Returns True if sent.
    """
    if customer is None or not getattr(customer, "whatsapp_consent", False):
        return False
    return whatsapp.send_template(
        shop=shop, to_phone=customer.phone, template_key=template_key,
        params=params or [], consent=True,
    )
