"""Order status lifecycle + append-only tracking history."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Order, OrderStatus, Role, TrackingEvent, User, utcnow

# Allowed forward transitions. Admin override bypasses this map but is recorded
# as is_override=True so the audit trail shows a human forced the state.
TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.CREATED: {OrderStatus.ASSIGNED, OrderStatus.CANCELLED},
    OrderStatus.ASSIGNED: {OrderStatus.PICKED_UP, OrderStatus.FAILED, OrderStatus.CANCELLED},
    OrderStatus.PICKED_UP: {OrderStatus.IN_TRANSIT, OrderStatus.FAILED},
    OrderStatus.IN_TRANSIT: {OrderStatus.OUT_FOR_DELIVERY, OrderStatus.FAILED},
    OrderStatus.OUT_FOR_DELIVERY: {OrderStatus.DELIVERED, OrderStatus.FAILED},
    OrderStatus.FAILED: {OrderStatus.RESCHEDULED, OrderStatus.CANCELLED},
    OrderStatus.RESCHEDULED: {OrderStatus.ASSIGNED, OrderStatus.OUT_FOR_DELIVERY, OrderStatus.CANCELLED},
    OrderStatus.DELIVERED: set(),
    OrderStatus.CANCELLED: set(),
}

# Statuses a delivery agent is allowed to set from the field.
AGENT_SETTABLE = {
    OrderStatus.PICKED_UP,
    OrderStatus.IN_TRANSIT,
    OrderStatus.OUT_FOR_DELIVERY,
    OrderStatus.DELIVERED,
    OrderStatus.FAILED,
}

TERMINAL = {OrderStatus.DELIVERED, OrderStatus.CANCELLED}


class InvalidTransition(Exception):
    pass


def can_transition(current: OrderStatus, target: OrderStatus) -> bool:
    return target in TRANSITIONS.get(current, set())


def record_event(
    db: Session,
    order: Order,
    *,
    status: OrderStatus,
    actor: User | None,
    note: str | None = None,
    location_text: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    is_override: bool = False,
    previous_status: OrderStatus | None = None,
) -> TrackingEvent:
    """Append one checkpoint, chained by hash to the previous one."""
    last = db.scalar(
        select(TrackingEvent)
        .where(TrackingEvent.order_id == order.id)
        .order_by(TrackingEvent.id.desc())
        .limit(1)
    )
    event = TrackingEvent(
        order_id=order.id,
        status=status,
        previous_status=previous_status,
        actor_id=actor.id if actor else None,
        actor_role=actor.role if actor else None,
        actor_name=actor.name if actor else "system",
        note=note,
        location_text=location_text,
        lat=lat,
        lng=lng,
        is_override=is_override,
        created_at=utcnow(),
        prev_hash=last.event_hash if last else None,
    )
    event.event_hash = event.compute_hash()
    db.add(event)
    db.flush()
    return event


def apply_status(
    db: Session,
    order: Order,
    *,
    status: OrderStatus,
    actor: User | None,
    note: str | None = None,
    location_text: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    override: bool = False,
) -> TrackingEvent:
    current = order.status
    if current == status and not override:
        raise InvalidTransition(f"Order is already {status.value}.")
    if not override and not can_transition(current, status):
        raise InvalidTransition(f"Cannot move an order from {current.value} to {status.value}.")

    order.status = status
    order.updated_at = utcnow()
    if status == OrderStatus.DELIVERED:
        order.delivered_at = utcnow()
    if status == OrderStatus.FAILED:
        order.delivery_attempts += 1

    return record_event(
        db,
        order,
        status=status,
        actor=actor,
        note=note,
        location_text=location_text,
        lat=lat,
        lng=lng,
        is_override=override,
        previous_status=current,
    )


def verify_chain(db: Session, order: Order) -> tuple[bool, int | None, int]:
    """Recompute the hash chain. Returns (intact, first_broken_event_id, count)."""
    events = db.scalars(
        select(TrackingEvent).where(TrackingEvent.order_id == order.id).order_by(TrackingEvent.id)
    ).all()
    prev_hash = None
    for ev in events:
        if ev.prev_hash != prev_hash or ev.compute_hash() != ev.event_hash:
            return False, ev.id, len(events)
        prev_hash = ev.event_hash
    return True, None, len(events)


def agent_may_set(actor: User, status: OrderStatus) -> bool:
    return actor.role == Role.AGENT and status in AGENT_SETTABLE
