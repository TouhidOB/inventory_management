"""
Delivery channels. Email uses Django's backend. SMS/WhatsApp are stubbed —
the interface exists so wiring Twilio / Meta WhatsApp later is a drop-in, but
nothing is sent until credentials are configured.
"""
import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger("notifications")


def send_email(to, subject, body):
    if not to:
        return False
    send_mail(
        subject, body,
        getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@stockwhisk.app"),
        [to], fail_silently=True,
    )
    return True


def send_sms(phone, body):
    """Stub. Wire Twilio here when TWILIO_* settings exist."""
    if not getattr(settings, "TWILIO_ACCOUNT_SID", ""):
        logger.info("SMS (stub) to %s: %s", phone, body)
        return False
    # TODO Phase 3: Twilio client send
    return False


def send_whatsapp(phone, body):
    """Stub. Wire Meta WhatsApp Business API here in Phase 3."""
    if not getattr(settings, "WHATSAPP_TOKEN", ""):
        logger.info("WhatsApp (stub) to %s: %s", phone, body)
        return False
    return False
