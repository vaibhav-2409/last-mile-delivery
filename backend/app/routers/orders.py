from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..deps import get_current_user
from ..models import (
    Order,
    OrderStatus,
    RescheduleRequest,
    Role,
    User,
    utcnow,
)
from ..schemas import (
    AssignRequest,
    MessageResponse,
    OrderCreate,
    OrderDetail,
    OrderSummary,
    QuoteRequest,
    QuoteResponse,
    RescheduleRequestIn,
    TrackingEventOut,
)
from ..services import assignment, lifecycle
from ..services.notifications import notify_status_change_bg
from ..services.rate_engine import RateConfigError, build_quote
from ..services.zones import ZoneNotFound

router = APIRouter(prefix="/api/orders", tags=["orders"])


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _new_order_code(db: Session) -> str:
    for _ in range(10):
        code = f"LM{datetime.now(timezone.utc):%y%m%d}{secrets.token_hex(3).upper()}"
        if not db.scalar(select(Order).where(Order.order_code == code)):
            return code
    raise HTTPException(status_code=500, detail="Could not allocate an order number. Try again.")


def _visible_order(db: Session, order_id: int, user: User) -> Order:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found.")
    if user.role == Role.CUSTOMER and order.customer_id != user.id:
        raise HTTPException(status_code=403, detail="This order belongs to another customer.")
    if user.role == Role.AGENT and order.agent_id != user.id:
        raise HTTPException(status_code=403, detail="This order is not assigned to you.")
    return order


def queue_notification(bg: BackgroundTasks, order: Order, status: OrderStatus, note: str | None = None):
    bg.add_task(notify_status_change_bg, SessionLocal, order.id, status, note)


def quote_or_422(db: Session, **kwargs):
    try:
        return build_quote(db, **kwargs)
    except ZoneNotFound as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RateConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


# --------------------------------------------------------------------------- #
# Quote — shown to the customer before they confirm
# --------------------------------------------------------------------------- #
@router.post("/quote", response_model=QuoteResponse)
def quote(payload: QuoteRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    q = quote_or_422(
        db,
        pickup_pincode=payload.pickup_pincode,
        drop_pincode=payload.drop_pincode,
        length_cm=payload.length_cm,
        breadth_cm=payload.breadth_cm,
        height_cm=payload.height_cm,
        actual_weight_kg=payload.actual_weight_kg,
        order_type=payload.order_type,
        payment_type=payload.payment_type,
    )
    return QuoteResponse(
        pickup_zone=q.pickup_zone,
        drop_zone=q.drop_zone,
        scope=q.scope,
        order_type=q.order_type,
        payment_type=q.payment_type,
        volumetric_weight_kg=q.volumetric_weight_kg,
        actual_weight_kg=q.actual_weight_kg,
        billable_weight_kg=q.billable_weight_kg,
        weight_basis=q.weight_basis,
        volumetric_divisor=q.volumetric_divisor,
        rate_card_id=q.rate_card.id,
        rate_card_name=q.rate_card.name,
        freight_charge=q.freight_charge,
        fuel_surcharge=q.fuel_surcharge,
        cod_surcharge=q.cod_surcharge,
        total_charge=q.total_charge,
        lines=[line.__dict__ for line in q.lines],
    )


# --------------------------------------------------------------------------- #
# Create
# --------------------------------------------------------------------------- #
@router.post("", response_model=OrderDetail, status_code=201)
def create_order(
    payload: OrderCreate,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Customers book for themselves. Admins may pass customer_id to book on
    behalf of someone. Charges are always recomputed server-side."""
    if user.role == Role.ADMIN:
        if not payload.customer_id:
            raise HTTPException(status_code=422, detail="Choose the customer this order is for.")
        customer = db.get(User, payload.customer_id)
        if not customer or customer.role != Role.CUSTOMER:
            raise HTTPException(status_code=404, detail="Customer not found.")
    elif user.role == Role.CUSTOMER:
        customer = user
    else:
        raise HTTPException(status_code=403, detail="Delivery agents cannot create orders.")

    q = quote_or_422(
        db,
        pickup_pincode=payload.pickup_pincode,
        drop_pincode=payload.drop_pincode,
        length_cm=payload.length_cm,
        breadth_cm=payload.breadth_cm,
        height_cm=payload.height_cm,
        actual_weight_kg=payload.actual_weight_kg,
        order_type=payload.order_type,
        payment_type=payload.payment_type,
    )

    order = Order(
        order_code=_new_order_code(db),
        customer_id=customer.id,
        created_by_id=user.id,
        pickup_contact=payload.pickup_contact,
        pickup_phone=payload.pickup_phone,
        pickup_address=payload.pickup_address,
        pickup_pincode=payload.pickup_pincode,
        pickup_zone_id=q.pickup_zone.id,
        pickup_lat=payload.pickup_lat,
        pickup_lng=payload.pickup_lng,
        drop_contact=payload.drop_contact,
        drop_phone=payload.drop_phone,
        drop_address=payload.drop_address,
        drop_pincode=payload.drop_pincode,
        drop_zone_id=q.drop_zone.id,
        drop_lat=payload.drop_lat,
        drop_lng=payload.drop_lng,
        length_cm=payload.length_cm,
        breadth_cm=payload.breadth_cm,
        height_cm=payload.height_cm,
        actual_weight_kg=payload.actual_weight_kg,
        volumetric_weight_kg=q.volumetric_weight_kg,
        billable_weight_kg=q.billable_weight_kg,
        weight_basis=q.weight_basis,
        order_type=payload.order_type,
        payment_type=payload.payment_type,
        rate_scope=q.scope,
        rate_card_id=q.rate_card.id,
        freight_charge=q.freight_charge,
        fuel_surcharge=q.fuel_surcharge,
        cod_surcharge=q.cod_surcharge,
        total_charge=q.total_charge,
        charge_breakdown=json.dumps(q.to_breakdown_dict()),
        package_description=payload.package_description,
        scheduled_date=payload.scheduled_date,
        status=OrderStatus.CREATED,
    )
    db.add(order)
    db.flush()

    lifecycle.record_event(
        db,
        order,
        status=OrderStatus.CREATED,
        actor=user,
        note=(
            f"Booked by {user.name} ({user.role.value.lower()}) · "
            f"{q.scope.value} {q.order_type.value} · billable {q.billable_weight_kg:g} kg "
            f"({q.weight_basis.lower()}) · total Rs {q.total_charge:.2f}"
        ),
        location_text=f"{q.pickup_zone.code} → {q.drop_zone.code}",
    )
    db.commit()
    db.refresh(order)
    queue_notification(bg, order, OrderStatus.CREATED)

    if payload.auto_assign:
        _auto_assign(db, order, user, bg)
        db.refresh(order)

    return order


def _auto_assign(db: Session, order: Order, actor: User, bg: BackgroundTasks, exclude: int | None = None):
    candidate = assignment.find_nearest_agent(db, order, exclude_agent_id=exclude)
    if candidate is None:
        return None
    assignment.attach_agent(db, order, candidate.profile.user_id)
    lifecycle.apply_status(
        db,
        order,
        status=OrderStatus.ASSIGNED,
        actor=actor,
        note=f"Auto-assigned to {candidate.profile.user.name} — {candidate.reason()}",
        override=order.status not in (OrderStatus.CREATED, OrderStatus.RESCHEDULED),
    )
    db.commit()
    db.refresh(order)
    queue_notification(bg, order, OrderStatus.ASSIGNED)
    return candidate


# --------------------------------------------------------------------------- #
# Read
# --------------------------------------------------------------------------- #
@router.get("", response_model=list[OrderSummary])
def list_orders(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    status_filter: OrderStatus | None = Query(default=None, alias="status"),
    zone_id: int | None = None,
    agent_id: int | None = None,
    customer_id: int | None = None,
    search: str | None = None,
    limit: int = Query(default=100, le=500),
    offset: int = 0,
):
    stmt = select(Order).order_by(Order.created_at.desc())

    if user.role == Role.CUSTOMER:
        stmt = stmt.where(Order.customer_id == user.id)
    elif user.role == Role.AGENT:
        stmt = stmt.where(Order.agent_id == user.id)
    else:  # admin filters
        if agent_id:
            stmt = stmt.where(Order.agent_id == agent_id)
        if customer_id:
            stmt = stmt.where(Order.customer_id == customer_id)

    if status_filter:
        stmt = stmt.where(Order.status == status_filter)
    if zone_id:
        stmt = stmt.where((Order.pickup_zone_id == zone_id) | (Order.drop_zone_id == zone_id))
    if search:
        like = f"%{search.strip()}%"
        stmt = stmt.where(
            Order.order_code.ilike(like)
            | Order.drop_pincode.ilike(like)
            | Order.pickup_pincode.ilike(like)
        )

    return db.scalars(stmt.limit(limit).offset(offset)).all()


@router.get("/{order_id}", response_model=OrderDetail)
def get_order(order_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _visible_order(db, order_id, user)


@router.get("/{order_id}/tracking", response_model=list[TrackingEventOut])
def get_tracking(order_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    order = _visible_order(db, order_id, user)
    return order.events


@router.get("/code/{order_code}", response_model=OrderDetail)
def get_by_code(order_code: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    order = db.scalar(select(Order).where(Order.order_code == order_code.upper()))
    if order is None:
        raise HTTPException(status_code=404, detail="No order with that number.")
    return _visible_order(db, order.id, user)


# --------------------------------------------------------------------------- #
# Customer actions
# --------------------------------------------------------------------------- #
@router.post("/{order_id}/reschedule", response_model=OrderDetail)
def reschedule(
    order_id: int,
    payload: RescheduleRequestIn,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Available after a failed attempt. Captures the new date and reassigns an
    agent — preferring someone other than the one who just failed."""
    order = _visible_order(db, order_id, user)
    if order.status != OrderStatus.FAILED:
        raise HTTPException(
            status_code=409, detail="Only a failed delivery can be rescheduled."
        )
    requested = payload.requested_date
    if requested.tzinfo is None:
        requested = requested.replace(tzinfo=timezone.utc)
    if requested < utcnow():
        raise HTTPException(status_code=422, detail="Pick a delivery date in the future.")

    previous_agent_id = order.agent_id
    order.scheduled_date = requested

    lifecycle.apply_status(
        db,
        order,
        status=OrderStatus.RESCHEDULED,
        actor=user,
        note=f"Customer rescheduled to {requested:%d %b %Y}"
        + (f" · reason: {payload.reason}" if payload.reason else ""),
    )

    candidate = assignment.find_nearest_agent(db, order, exclude_agent_id=previous_agent_id)
    new_agent_id = None
    if candidate:
        # capacity for the previous agent was already released when the attempt failed
        assignment.attach_agent(db, order, candidate.profile.user_id, release_previous=False)
        new_agent_id = candidate.profile.user_id

    db.add(
        RescheduleRequest(
            order_id=order.id,
            requested_date=requested,
            reason=payload.reason,
            requested_by_id=user.id,
            previous_agent_id=previous_agent_id,
            new_agent_id=new_agent_id,
            attempt_number=order.delivery_attempts + 1,
        )
    )
    db.commit()
    db.refresh(order)
    queue_notification(bg, order, OrderStatus.RESCHEDULED)

    if candidate:
        lifecycle.apply_status(
            db,
            order,
            status=OrderStatus.ASSIGNED,
            actor=user,
            note=f"Reassigned to {candidate.profile.user.name} for attempt "
            f"{order.delivery_attempts + 1} — {candidate.reason()}",
        )
        db.commit()
        db.refresh(order)
        queue_notification(bg, order, OrderStatus.ASSIGNED)

    return order


@router.post("/{order_id}/cancel", response_model=MessageResponse)
def cancel(
    order_id: int,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    order = _visible_order(db, order_id, user)
    try:
        lifecycle.apply_status(
            db, order, status=OrderStatus.CANCELLED, actor=user, note="Cancelled by customer"
        )
    except lifecycle.InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    assignment.release_agent(db, order.agent_id)
    db.commit()
    queue_notification(bg, order, OrderStatus.CANCELLED)
    return MessageResponse(message=f"Order {order.order_code} cancelled.")


# --------------------------------------------------------------------------- #
# Assignment (admin) — kept here so all order mutations sit together
# --------------------------------------------------------------------------- #
@router.post("/{order_id}/assign", response_model=OrderDetail)
def assign_agent(
    order_id: int,
    payload: AssignRequest,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.role != Role.ADMIN:
        raise HTTPException(status_code=403, detail="Only an admin can assign agents.")
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found.")
    if order.status in lifecycle.TERMINAL:
        raise HTTPException(status_code=409, detail=f"Order is already {order.status.value}.")

    agent = db.get(User, payload.agent_id)
    if not agent or agent.role != Role.AGENT:
        raise HTTPException(status_code=404, detail="Delivery agent not found.")

    assignment.attach_agent(db, order, agent.id)
    if order.status == OrderStatus.ASSIGNED:
        lifecycle.record_event(
            db,
            order,
            status=OrderStatus.ASSIGNED,
            actor=user,
            note=f"Reassigned to {agent.name} by {user.name}",
            previous_status=OrderStatus.ASSIGNED,
        )
    else:
        try:
            lifecycle.apply_status(
                db,
                order,
                status=OrderStatus.ASSIGNED,
                actor=user,
                note=f"Manually assigned to {agent.name} by {user.name}",
                override=order.status not in (OrderStatus.CREATED, OrderStatus.RESCHEDULED),
            )
        except lifecycle.InvalidTransition as exc:
            raise HTTPException(status_code=409, detail=str(exc))
    db.commit()
    db.refresh(order)
    queue_notification(bg, order, OrderStatus.ASSIGNED)
    return order


@router.post("/{order_id}/auto-assign", response_model=OrderDetail)
def auto_assign(
    order_id: int,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.role != Role.ADMIN:
        raise HTTPException(status_code=403, detail="Only an admin can run auto-assignment.")
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found.")
    if order.status in lifecycle.TERMINAL:
        raise HTTPException(status_code=409, detail=f"Order is already {order.status.value}.")

    candidate = _auto_assign(db, order, user, bg)
    if candidate is None:
        raise HTTPException(
            status_code=409,
            detail="No agent is available with spare capacity. Free up an agent and try again.",
        )
    db.refresh(order)
    return order
