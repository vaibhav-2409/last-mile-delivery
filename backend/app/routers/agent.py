from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_agent
from ..models import AgentProfile, Order, OrderStatus, User, utcnow
from ..schemas import AgentProfileOut, AgentUpdate, OrderDetail, OrderSummary, StatusUpdateRequest
from ..services import assignment, lifecycle
from .orders import queue_notification

router = APIRouter(prefix="/api/agent", tags=["agent"])


def _profile(db: Session, user: User) -> AgentProfile:
    profile = db.scalar(select(AgentProfile).where(AgentProfile.user_id == user.id))
    if profile is None:
        raise HTTPException(status_code=404, detail="No agent profile set up for this account.")
    return profile


@router.get("/me", response_model=AgentProfileOut)
def my_profile(db: Session = Depends(get_db), user: User = Depends(require_agent)):
    return _profile(db, user)


@router.patch("/me", response_model=AgentProfileOut)
def update_my_profile(
    payload: AgentUpdate, db: Session = Depends(get_db), user: User = Depends(require_agent)
):
    """Agents toggle their own availability and push GPS fixes from the app."""
    profile = _profile(db, user)
    data = payload.model_dump(exclude_unset=True)
    data.pop("max_active_orders", None)  # capacity is an admin decision
    for key, value in data.items():
        setattr(profile, key, value)
    if "current_lat" in data or "current_lng" in data:
        profile.location_updated_at = utcnow()
    db.commit()
    db.refresh(profile)
    return profile


@router.get("/orders", response_model=list[OrderSummary])
def my_orders(
    db: Session = Depends(get_db),
    user: User = Depends(require_agent),
    active_only: bool = False,
):
    stmt = select(Order).where(Order.agent_id == user.id).order_by(Order.created_at.desc())
    if active_only:
        stmt = stmt.where(Order.status.notin_(list(lifecycle.TERMINAL)))
    return db.scalars(stmt).all()


@router.post("/orders/{order_id}/status", response_model=OrderDetail)
def update_status(
    order_id: int,
    payload: StatusUpdateRequest,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_agent),
):
    """Field status update. Agents can only move their own orders, and only
    through the field statuses."""
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found.")
    if order.agent_id != user.id:
        raise HTTPException(status_code=403, detail="This order is not assigned to you.")
    if not lifecycle.agent_may_set(user, payload.status):
        raise HTTPException(
            status_code=403, detail=f"Agents cannot set an order to {payload.status.value}."
        )
    if payload.status == OrderStatus.FAILED and not (payload.failure_reason or payload.note):
        raise HTTPException(status_code=422, detail="Record why the delivery failed.")

    note = payload.note
    if payload.status == OrderStatus.FAILED:
        order.failure_reason = payload.failure_reason or payload.note
        # surface the reason on the timeline, not just on the order record
        note = payload.failure_reason or payload.note

    try:
        lifecycle.apply_status(
            db,
            order,
            status=payload.status,
            actor=user,
            note=note,
            location_text=payload.location_text,
            lat=payload.lat,
            lng=payload.lng,
        )
    except lifecycle.InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # free capacity once the parcel is off the agent's plate
    if payload.status in (OrderStatus.DELIVERED, OrderStatus.FAILED):
        assignment.release_agent(db, user.id)

    if payload.lat is not None and payload.lng is not None:
        profile = db.scalar(select(AgentProfile).where(AgentProfile.user_id == user.id))
        if profile:
            profile.current_lat = payload.lat
            profile.current_lng = payload.lng
            profile.location_updated_at = utcnow()

    db.commit()
    db.refresh(order)
    queue_notification(bg, order, payload.status, payload.note)
    return order
