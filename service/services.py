"""Service layer for warranties + service tickets."""
from django.db import transaction
from django.utils import timezone

from audit.models import AuditLog
from audit.services import record
from inventory.models import MovementType
from inventory.services import apply_movement
from notifications.models import NotificationType
from notifications.services import notify, notify_customer_whatsapp

from .models import (
    ServiceTicket,
    ServiceTicketPart,
    ServiceTicketStatusHistory,
    Warranty,
)


def _next_ticket_no(shop):
    count = ServiceTicket.all_objects.filter(shop_id=shop.id).count() + 1
    return f"SVC-{count:06d}"


# --- Warranty ---------------------------------------------------------------

def create_warranty_for_sale_item(*, sale_item, period_months=12, serial_no="", terms=""):
    """Register a warranty from a sold line."""
    return Warranty.all_objects.create(
        shop_id=sale_item.shop_id, sale_item=sale_item, product=sale_item.product,
        customer=sale_item.sale.customer, period_months=period_months,
        start_date=sale_item.sale.sale_date.date(), serial_no=serial_no, terms=terms,
    )


def lookup_warranties(shop, *, phone=None, invoice_no=None):
    """Counter lookup by customer phone or sale invoice number (staff only)."""
    qs = Warranty.all_objects.filter(shop_id=shop.id).select_related("product", "customer")
    if phone:
        qs = qs.filter(customer__phone=phone)
    if invoice_no:
        qs = qs.filter(sale_item__sale__invoice_no=invoice_no)
    return qs


# --- Service tickets --------------------------------------------------------

@transaction.atomic
def create_ticket(*, shop, customer, device_description, complaint, branch=None,
                  technician=None, service_charge=0, estimated_delivery=None, created_by=None,
                  customer_name="", customer_phone="", device_type="", issue_type="", warranty=None):
    # received_at defaults to now automatically (item 12); expected delivery is
    # supplied manually. customer_name/phone allow walk-ins without a record.
    ticket = ServiceTicket.objects.create(
        shop=shop, customer=customer, branch=branch,
        ticket_no=_next_ticket_no(shop), device_description=device_description,
        complaint=complaint, technician=technician, service_charge=service_charge,
        estimated_delivery=estimated_delivery, created_by=created_by,
        customer_name=customer_name, customer_phone=customer_phone,
        device_type=device_type or ServiceTicket.DeviceType.OTHER,
        issue_type=issue_type or ServiceTicket.IssueType.OTHER,
        warranty=warranty,
    )
    ServiceTicketStatusHistory.objects.create(
        shop=shop, ticket=ticket, from_status="", to_status=ticket.status, changed_by=created_by
    )
    return ticket


@transaction.atomic
def change_ticket_status(*, ticket, new_status, note="", changed_by=None):
    old = ticket.status
    if new_status == old:
        return ticket
    ticket.status = new_status
    if new_status == ServiceTicket.Status.DELIVERED:
        ticket.actual_delivery = timezone.now()
    ticket.save(update_fields=["status", "actual_delivery"])

    ServiceTicketStatusHistory.objects.create(
        shop=ticket.shop, ticket=ticket, from_status=old, to_status=new_status,
        note=note, changed_by=changed_by,
    )
    record(
        action=AuditLog.Action.UPDATE, actor=changed_by, shop=ticket.shop, target=ticket,
        description=f"Ticket {ticket.ticket_no}: {old} -> {new_status}",
        changes={"status": [old, new_status]},
    )
    # Notify the customer on key transitions (WhatsApp added in 9.5).
    if new_status in ServiceTicket.NOTIFY_STATUSES and ticket.customer_id:
        notify(
            shop=ticket.shop, type=NotificationType.GENERAL,
            title=f"Service update: {ticket.get_status_display()}",
            message=f"Your device '{ticket.device_description}' (ticket {ticket.ticket_no}) "
                    f"is now {ticket.get_status_display()}.",
            metadata={"ticket_id": ticket.id, "customer_id": ticket.customer_id},
        )
        # WhatsApp (approved template) to the customer if they opted in (9.5).
        notify_customer_whatsapp(
            shop=ticket.shop, customer=ticket.customer,
            template_key="service_ticket_update",
            params=[ticket.ticket_no, ticket.get_status_display()],
        )
    return ticket


@transaction.atomic
def add_ticket_part(*, ticket, product, quantity=1, unit_cost=None, unit_price=None,
                    from_stock=True, created_by=None):
    """Record a part on a ticket; deduct from stock ledger when from_stock.
    ``unit_price`` is the customer charge (defaults to the product's sell price)."""
    unit_cost = product.cost_price if unit_cost is None else unit_cost
    unit_price = product.selling_price if unit_price is None else unit_price
    part = ServiceTicketPart.objects.create(
        shop=ticket.shop, ticket=ticket, product=product,
        quantity=quantity, unit_cost=unit_cost, unit_price=unit_price, from_stock=from_stock,
    )
    if from_stock and product.track_inventory:
        apply_movement(
            shop=ticket.shop, product=product, movement_type=MovementType.ADJUST_OUT,
            quantity=quantity, unit_cost=unit_cost, reference_type="ServiceTicket",
            reference_id=ticket.id, note=f"Part used on {ticket.ticket_no}", created_by=created_by,
        )
    return part


@transaction.atomic
def add_ticket_payment(*, ticket, amount, method="cash", created_by=None):
    """Collect a payment against a ticket bill. Increments ``paid``, caps at the
    outstanding due, and posts a cash-in ledger entry."""
    from decimal import Decimal
    from accounting.models import LedgerEntry
    amount = Decimal(amount)
    if amount <= 0:
        raise ValueError("Amount must be positive.")
    if amount > ticket.due:
        raise ValueError(f"Amount exceeds outstanding due of {ticket.due}.")
    ticket.paid = (ticket.paid or Decimal("0")) + amount
    ticket.save(update_fields=["paid", "updated_at"])
    LedgerEntry.objects.create(
        shop=ticket.shop, account=LedgerEntry.Account.CASH, amount=amount,
        source_type="ServiceTicket", source_id=str(ticket.id),
        description=f"Payment for ticket {ticket.ticket_no}",
    )
    return ticket


def refresh_warranty_statuses(shop, soon_days=30):
    """Recompute and persist warranty statuses (called by the daily task)."""
    updated = 0
    for w in Warranty.all_objects.filter(shop_id=shop.id).exclude(
        status__in=[Warranty.Status.CLAIMED, Warranty.Status.VOID]
    ):
        new = w.compute_status(soon_days)
        if new != w.status:
            w.status = new
            w.save(update_fields=["status"])
            updated += 1
    return updated
