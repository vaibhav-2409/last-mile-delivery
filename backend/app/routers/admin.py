from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_admin
from ..models import (
    AgentProfile,
    Area,
    CodRule,
    Notification,
    Order,
    OrderStatus,
    RateCard,
    Role,
    SystemSetting,
    TrackingEvent,
    User,
    Zone,
    utcnow,
)
from ..schemas import (
    AgentCreate,
    AgentProfileOut,
    AgentUpdate,
    AreaCreate,
    AreaOut,
    AreaUpdate,
    CodRuleOut,
    CodRuleUpsert,
    IntegrityReport,
    MessageResponse,
    NotificationOut,
    OrderDetail,
    RateCardCreate,
    RateCardOut,
    RateCardUpdate,
    SettingOut,
    SettingUpsert,
    StatusUpdateRequest,
    UserOut,
    ZoneCreate,
    ZoneOut,
    ZoneUpdate,
)
from ..security import hash_password
from ..services import assignment, lifecycle
from .orders import queue_notification

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _patch(obj, payload):
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(obj, key, value)


# --------------------------------------------------------------------------- #
# Zones
# --------------------------------------------------------------------------- #
@router.get("/zones", response_model=list[ZoneOut])
def list_zones(db: Session = Depends(get_db)):
    return db.scalars(select(Zone).order_by(Zone.code)).all()


@router.post("/zones", response_model=ZoneOut, status_code=201)
def create_zone(payload: ZoneCreate, db: Session = Depends(get_db)):
    if db.scalar(select(Zone).where(Zone.code == payload.code.upper())):
        raise HTTPException(status_code=409, detail="A zone with that code already exists.")
    zone = Zone(**payload.model_dump())
    zone.code = zone.code.upper()
    db.add(zone)
    db.commit()
    db.refresh(zone)
    return zone


@router.patch("/zones/{zone_id}", response_model=ZoneOut)
def update_zone(zone_id: int, payload: ZoneUpdate, db: Session = Depends(get_db)):
    zone = db.get(Zone, zone_id)
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found.")
    _patch(zone, payload)
    db.commit()
    db.refresh(zone)
    return zone


@router.delete("/zones/{zone_id}", response_model=MessageResponse)
def delete_zone(zone_id: int, db: Session = Depends(get_db)):
    zone = db.get(Zone, zone_id)
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found.")
    in_use = db.scalar(
        select(func.count(Order.id)).where(
            (Order.pickup_zone_id == zone_id) | (Order.drop_zone_id == zone_id)
        )
    )
    if in_use:
        raise HTTPException(
            status_code=409,
            detail=f"{in_use} orders reference this zone. Deactivate it instead of deleting.",
        )
    db.delete(zone)
    db.commit()
    return MessageResponse(message="Zone deleted.")


# --------------------------------------------------------------------------- #
# Areas (pincode -> zone mapping)
# --------------------------------------------------------------------------- #
@router.get("/areas", response_model=list[AreaOut])
def list_areas(zone_id: int | None = None, db: Session = Depends(get_db)):
    stmt = select(Area).order_by(Area.pincode)
    if zone_id:
        stmt = stmt.where(Area.zone_id == zone_id)
    return db.scalars(stmt).all()


@router.post("/areas", response_model=AreaOut, status_code=201)
def create_area(payload: AreaCreate, db: Session = Depends(get_db)):
    if db.scalar(select(Area).where(Area.pincode == payload.pincode)):
        raise HTTPException(status_code=409, detail="That pincode is already mapped to a zone.")
    if not db.get(Zone, payload.zone_id):
        raise HTTPException(status_code=404, detail="Zone not found.")
    area = Area(**payload.model_dump())
    db.add(area)
    db.commit()
    db.refresh(area)
    return area


@router.patch("/areas/{area_id}", response_model=AreaOut)
def update_area(area_id: int, payload: AreaUpdate, db: Session = Depends(get_db)):
    area = db.get(Area, area_id)
    if not area:
        raise HTTPException(status_code=404, detail="Area not found.")
    _patch(area, payload)
    db.commit()
    db.refresh(area)
    return area


@router.delete("/areas/{area_id}", response_model=MessageResponse)
def delete_area(area_id: int, db: Session = Depends(get_db)):
    area = db.get(Area, area_id)
    if not area:
        raise HTTPException(status_code=404, detail="Area not found.")
    db.delete(area)
    db.commit()
    return MessageResponse(message="Area removed.")


# --------------------------------------------------------------------------- #
# Rate cards
# --------------------------------------------------------------------------- #
@router.get("/rate-cards", response_model=list[RateCardOut])
def list_rate_cards(db: Session = Depends(get_db)):
    return db.scalars(
        select(RateCard).order_by(RateCard.order_type, RateCard.scope, RateCard.id)
    ).all()


@router.post("/rate-cards", response_model=RateCardOut, status_code=201)
def create_rate_card(payload: RateCardCreate, db: Session = Depends(get_db)):
    data = payload.model_dump()
    for field in ("from_zone_id", "to_zone_id"):
        if data[field] and not db.get(Zone, data[field]):
            raise HTTPException(status_code=404, detail=f"Zone in {field} not found.")
    card = RateCard(**data)
    db.add(card)
    db.commit()
    db.refresh(card)
    return card


@router.patch("/rate-cards/{card_id}", response_model=RateCardOut)
def update_rate_card(card_id: int, payload: RateCardUpdate, db: Session = Depends(get_db)):
    card = db.get(RateCard, card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Rate card not found.")
    _patch(card, payload)
    db.commit()
    db.refresh(card)
    return card


@router.delete("/rate-cards/{card_id}", response_model=MessageResponse)
def delete_rate_card(card_id: int, db: Session = Depends(get_db)):
    card = db.get(RateCard, card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Rate card not found.")
    card.is_active = False  # priced orders still point here, so retire rather than delete
    db.commit()
    return MessageResponse(message="Rate card deactivated.")


# --------------------------------------------------------------------------- #
# COD rules + engine settings
# --------------------------------------------------------------------------- #
@router.get("/cod-rules", response_model=list[CodRuleOut])
def list_cod_rules(db: Session = Depends(get_db)):
    return db.scalars(select(CodRule).order_by(CodRule.order_type)).all()


@router.put("/cod-rules", response_model=CodRuleOut)
def upsert_cod_rule(payload: CodRuleUpsert, db: Session = Depends(get_db)):
    rule = db.scalar(select(CodRule).where(CodRule.order_type == payload.order_type))
    if rule is None:
        rule = CodRule(**payload.model_dump())
        db.add(rule)
    else:
        _patch(rule, payload)
    db.commit()
    db.refresh(rule)
    return rule


@router.get("/settings", response_model=list[SettingOut])
def list_settings(db: Session = Depends(get_db)):
    return db.scalars(select(SystemSetting).order_by(SystemSetting.key)).all()


@router.put("/settings", response_model=SettingOut)
def upsert_setting(payload: SettingUpsert, db: Session = Depends(get_db)):
    row = db.get(SystemSetting, payload.key)
    if row is None:
        row = SystemSetting(**payload.model_dump())
        db.add(row)
    else:
        row.value = payload.value
        if payload.description:
            row.description = payload.description
    db.commit()
    db.refresh(row)
    return row


# --------------------------------------------------------------------------- #
# People
# --------------------------------------------------------------------------- #
@router.get("/customers", response_model=list[UserOut])
def list_customers(db: Session = Depends(get_db), search: str | None = None):
    stmt = select(User).where(User.role == Role.CUSTOMER).order_by(User.name)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(User.name.ilike(like) | User.email.ilike(like))
    return db.scalars(stmt).all()


@router.get("/agents", response_model=list[AgentProfileOut])
def list_agents(db: Session = Depends(get_db), available_only: bool = False):
    stmt = select(AgentProfile).join(User, User.id == AgentProfile.user_id).order_by(User.name)
    if available_only:
        stmt = stmt.where(AgentProfile.is_available.is_(True))
    return db.scalars(stmt).all()


@router.post("/agents", response_model=AgentProfileOut, status_code=201)
def create_agent(payload: AgentCreate, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.email == payload.email.lower())):
        raise HTTPException(status_code=409, detail="That email is already registered.")
    user = User(
        name=payload.name,
        email=payload.email.lower(),
        phone=payload.phone,
        password_hash=hash_password(payload.password),
        role=Role.AGENT,
    )
    db.add(user)
    db.flush()
    profile = AgentProfile(
        user_id=user.id,
        home_zone_id=payload.home_zone_id,
        vehicle_type=payload.vehicle_type,
        max_active_orders=payload.max_active_orders,
        current_lat=payload.current_lat,
        current_lng=payload.current_lng,
        location_updated_at=utcnow() if payload.current_lat is not None else None,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


@router.patch("/agents/{agent_user_id}", response_model=AgentProfileOut)
def update_agent(agent_user_id: int, payload: AgentUpdate, db: Session = Depends(get_db)):
    profile = db.scalar(select(AgentProfile).where(AgentProfile.user_id == agent_user_id))
    if not profile:
        raise HTTPException(status_code=404, detail="Agent not found.")
    _patch(profile, payload)
    db.commit()
    db.refresh(profile)
    return profile


# --------------------------------------------------------------------------- #
# Order overrides + oversight
# --------------------------------------------------------------------------- #
@router.post("/orders/{order_id}/override-status", response_model=OrderDetail)
def override_status(
    order_id: int,
    payload: StatusUpdateRequest,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Force any order into any status. The transition map is bypassed, but the
    event is stamped is_override=True so the history shows who forced it."""
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")
    previous = order.status
    if payload.status == OrderStatus.FAILED:
        order.failure_reason = payload.failure_reason or payload.note or "Marked failed by admin"

    lifecycle.apply_status(
        db,
        order,
        status=payload.status,
        actor=admin,
        note=payload.note or f"Status forced from {previous.value} by {admin.name}",
        location_text=payload.location_text,
        override=True,
    )
    if payload.status in (OrderStatus.DELIVERED, OrderStatus.FAILED, OrderStatus.CANCELLED):
        if previous not in (OrderStatus.DELIVERED, OrderStatus.FAILED, OrderStatus.CANCELLED):
            assignment.release_agent(db, order.agent_id)
    db.commit()
    db.refresh(order)
    queue_notification(bg, order, payload.status, payload.note)
    return order


@router.get("/orders/{order_id}/integrity", response_model=IntegrityReport)
def verify_tracking_integrity(order_id: int, db: Session = Depends(get_db)):
    """Recompute the tracking hash chain to prove history has not been edited."""
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")
    intact, broken_at, count = lifecycle.verify_chain(db, order)
    return IntegrityReport(
        order_code=order.order_code, events=count, intact=intact, broken_at=broken_at
    )


@router.get("/notifications", response_model=list[NotificationOut])
def list_notifications(
    db: Session = Depends(get_db),
    order_id: int | None = None,
    limit: int = Query(default=100, le=500),
):
    stmt = select(Notification).order_by(Notification.created_at.desc()).limit(limit)
    if order_id:
        stmt = stmt.where(Notification.order_id == order_id)
    return db.scalars(stmt).all()


@router.get("/stats")
def dashboard_stats(db: Session = Depends(get_db)):
    by_status = dict(
        db.execute(select(Order.status, func.count(Order.id)).group_by(Order.status)).all()
    )
    revenue = db.scalar(
        select(func.coalesce(func.sum(Order.total_charge), 0.0)).where(
            Order.status != OrderStatus.CANCELLED
        )
    )
    return {
        "orders_total": db.scalar(select(func.count(Order.id))) or 0,
        "orders_by_status": {k.value: v for k, v in by_status.items()},
        "revenue_booked": round(revenue or 0.0, 2),
        "zones": db.scalar(select(func.count(Zone.id))) or 0,
        "areas": db.scalar(select(func.count(Area.id))) or 0,
        "agents_available": db.scalar(
            select(func.count(AgentProfile.id)).where(AgentProfile.is_available.is_(True))
        )
        or 0,
        "agents_total": db.scalar(select(func.count(AgentProfile.id))) or 0,
        "customers": db.scalar(select(func.count(User.id)).where(User.role == Role.CUSTOMER)) or 0,
        "tracking_events": db.scalar(select(func.count(TrackingEvent.id))) or 0,
        "notifications_sent": db.scalar(select(func.count(Notification.id))) or 0,
    }
