"""Email + SMS notifications.

Every notification is written to the notifications table whether or not a
provider is configured. With no SMTP/SMS credentials the row is stored with
status SIMULATED and printed to the log, so the full flow is demonstrable on a
free-tier host without secrets. Sending happens on a background task so a slow
SMTP handshake never blocks the API response.
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    Notification,
    NotificationChannel,
    NotificationStatus,
    Order,
    OrderStatus,
)

log = logging.getLogger("lastmile.notifications")

STATUS_COPY: dict[OrderStatus, tuple[str, str]] = {
    OrderStatus.CREATED: (
        "Order {code} confirmed",
        "We have your booking. Charges of Rs {total} are confirmed and we will assign an agent shortly.",
    ),
    OrderStatus.ASSIGNED: (
        "Agent assigned to order {code}",
        "{agent} will handle your shipment. Pickup is scheduled from {pickup_pin}.",
    ),
    OrderStatus.PICKED_UP: (
        "Order {code} picked up",
        "Your package has left {pickup_pin} and is with our agent.",
    ),
    OrderStatus.IN_TRANSIT: (
        "Order {code} in transit",
        "Your package is moving towards {drop_pin}.",
    ),
    OrderStatus.OUT_FOR_DELIVERY: (
        "Order {code} is out for delivery",
        "{agent} is delivering your package today. Keep your phone reachable.",
    ),
    OrderStatus.DELIVERED: (
        "Order {code} delivered",
        "Your package was delivered. Thanks for shipping with us.",
    ),
    OrderStatus.FAILED: (
        "Delivery attempt failed for order {code}",
        "We could not complete attempt {attempts}. Reason: {reason}. "
        "Open your dashboard to pick a new delivery date and we will reassign an agent.",
    ),
    OrderStatus.RESCHEDULED: (
        "Order {code} rescheduled",
        "Your delivery is rescheduled for {scheduled}. A new agent has been assigned.",
    ),
    OrderStatus.CANCELLED: (
        "Order {code} cancelled",
        "This order has been cancelled. No further action is needed.",
    ),
}


def _fmt(template: str, order: Order) -> str:
    return template.format(
        code=order.order_code,
        total=f"{order.total_charge:.2f}",
        agent=order.agent.name if order.agent else "An agent",
        pickup_pin=order.pickup_pincode,
        drop_pin=order.drop_pincode,
        attempts=order.delivery_attempts,
        reason=order.failure_reason or "not recorded",
        scheduled=order.scheduled_date.strftime("%d %b %Y") if order.scheduled_date else "the new date",
        status=order.status.value.replace("_", " ").title(),
    )


def build_message(order: Order, status: OrderStatus, extra_note: str | None = None) -> tuple[str, str]:
    subject_t, body_t = STATUS_COPY.get(
        status, ("Update on order {code}", "Your order is now {status}.")
    )
    subject = _fmt(subject_t, order)
    body = _fmt(body_t, order)
    if extra_note:
        body += f"\n\nNote from the team: {extra_note}"
    body += (
        f"\n\nTrack it here: {settings.PUBLIC_BASE_URL}/?track={order.order_code}"
        f"\n\n-- {settings.MAIL_FROM_NAME}"
    )
    return subject, body


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #
def _send_email(to: str, subject: str, body: str) -> tuple[NotificationStatus, str]:
    if not settings.email_configured:
        log.info("[EMAIL SIMULATED] to=%s subject=%s", to, subject)
        return NotificationStatus.SIMULATED, "SMTP not configured; payload logged only."
    try:
        msg = EmailMessage()
        msg["From"] = f"{settings.MAIL_FROM_NAME} <{settings.MAIL_FROM}>"
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
            if settings.SMTP_USE_TLS:
                server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg)
        return NotificationStatus.SENT, "accepted by SMTP relay"
    except Exception as exc:  # noqa: BLE001 - never let comms break the order flow
        log.warning("Email send failed: %s", exc)
        return NotificationStatus.FAILED, str(exc)[:400]


def _send_sms(to: str, body: str) -> tuple[NotificationStatus, str]:
    if not settings.sms_configured:
        log.info("[SMS SIMULATED] to=%s body=%s", to, body[:80])
        return NotificationStatus.SIMULATED, "SMS provider not configured; payload logged only."
    try:
        import requests

        if settings.SMS_PROVIDER == "twilio":
            resp = requests.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_ACCOUNT_SID}/Messages.json",
                data={"From": settings.TWILIO_FROM_NUMBER, "To": to, "Body": body[:1500]},
                auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
                timeout=15,
            )
        else:  # fast2sms
            resp = requests.post(
                "https://www.fast2sms.com/dev/bulkV2",
                headers={"authorization": settings.FAST2SMS_API_KEY},
                data={"route": "q", "message": body[:300], "numbers": to.lstrip("+91")},
                timeout=15,
            )
        ok = resp.status_code < 300
        return (NotificationStatus.SENT if ok else NotificationStatus.FAILED), resp.text[:400]
    except Exception as exc:  # noqa: BLE001
        log.warning("SMS send failed: %s", exc)
        return NotificationStatus.FAILED, str(exc)[:400]


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def notify_status_change(
    db: Session, order: Order, status: OrderStatus, extra_note: str | None = None
) -> list[Notification]:
    """Persist + dispatch the customer email (and SMS when a phone is on file)."""
    if not settings.NOTIFICATIONS_ENABLED:
        return []

    subject, body = build_message(order, status, extra_note)
    created: list[Notification] = []

    customer = order.customer
    if customer and customer.email:
        state, detail = _send_email(customer.email, subject, body)
        created.append(
            Notification(
                order_id=order.id,
                channel=NotificationChannel.EMAIL,
                recipient=customer.email,
                subject=subject,
                body=body,
                status=state,
                provider_response=detail,
                trigger_status=status,
            )
        )

    phone = (customer.phone if customer else None) or order.drop_phone
    if phone:
        sms_body = f"{subject}. {body.splitlines()[0]}"
        state, detail = _send_sms(phone, sms_body)
        created.append(
            Notification(
                order_id=order.id,
                channel=NotificationChannel.SMS,
                recipient=phone,
                subject=subject,
                body=sms_body,
                status=state,
                provider_response=detail,
                trigger_status=status,
            )
        )

    for row in created:
        db.add(row)
    db.commit()
    return created


def notify_status_change_bg(session_factory, order_id: int, status: OrderStatus, note: str | None = None):
    """Background-task entry point: opens its own session."""
    db = session_factory()
    try:
        order = db.get(Order, order_id)
        if order:
            notify_status_change(db, order, status, note)
    finally:
        db.close()
