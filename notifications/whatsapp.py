"""
WhatsApp Business (Meta) channel (9.5).

Template messages must be pre-approved with Meta before use. Sending resolves
the sender: shop's own credentials (Enterprise) or the shared platform number.
Real Graph API call happens only when credentials are present; otherwise it is
logged as a stub so the flow works end-to-end in dev/tests.

Consent: callers must pass only opted-in recipients. This module refuses to
send when ``consent`` is False as a defensive backstop.
"""
import logging

from django.conf import settings

logger = logging.getLogger("notifications.whatsapp")

# Approved-template registry. These names must match templates approved in the
# Meta Business Manager before production use.
TEMPLATES = {
    "low_stock_alert": "stockwhisk_low_stock_alert",
    "subscription_expiry": "stockwhisk_subscription_expiry",
    "service_ticket_update": "stockwhisk_service_ticket_update",
    "warranty_expiry": "stockwhisk_warranty_expiry",
    "invoice_receipt": "stockwhisk_invoice_receipt",
}


def _resolve_sender(shop):
    """Return (phone_number_id, access_token) for the shop, or (None, None)."""
    from .models import ShopWhatsAppConfig

    cfg = ShopWhatsAppConfig.all_objects.filter(shop_id=shop.id).first()
    if cfg and cfg.enabled and not cfg.use_platform_number and cfg.phone_number_id and cfg.access_token:
        return cfg.phone_number_id, cfg.access_token
    # Platform shared number.
    return (getattr(settings, "WHATSAPP_PHONE_ID", ""), getattr(settings, "WHATSAPP_TOKEN", ""))


def send_template(*, shop, to_phone, template_key, params=None, consent=True):
    """
    Send an approved template message. Returns True if actually dispatched.
    No-op (returns False) when consent is missing or credentials are absent.
    """
    if not consent:
        logger.info("WhatsApp skipped (no consent) to %s", to_phone)
        return False
    if not to_phone:
        return False

    template_name = TEMPLATES.get(template_key, template_key)
    phone_id, token = _resolve_sender(shop)
    if not (phone_id and token):
        logger.info("WhatsApp (stub) template=%s to=%s params=%s", template_name, to_phone, params)
        return False

    try:
        import requests

        url = f"{settings.WHATSAPP_API_URL}/{phone_id}/messages"
        components = []
        if params:
            components = [{
                "type": "body",
                "parameters": [{"type": "text", "text": str(p)} for p in params],
            }]
        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "template",
            "template": {"name": template_name, "language": {"code": "en"},
                         "components": components},
        }
        resp = requests.post(url, json=payload, headers={"Authorization": f"Bearer {token}"}, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as exc:  # network/API — log, never break the caller
        logger.warning("WhatsApp send failed: %s", exc)
        return False


def verify_webhook(mode, token, challenge):
    """Meta webhook verification handshake."""
    if mode == "subscribe" and token == getattr(settings, "WHATSAPP_VERIFY_TOKEN", ""):
        return challenge
    return None
