"""Domain model.

Design notes
------------
* Zones and Areas are data, not code. Zone detection is a pincode -> area -> zone
  lookup, so operations can re-map a pincode without a deploy.
* RateCard rows are keyed by (order_type, scope, from_zone, to_zone) which lets an
  admin configure intra/inter rates separately for B2B and B2C, plus optional
  lane-specific overrides. Nothing about pricing lives in Python constants.
* TrackingEvent is append-only. Updates and deletes are blocked at the ORM layer
  and each row carries a hash chained to the previous event of the same order, so
  any tampering done directly in the database is detectable.
"""
from __future__ import annotations

import enum
import hashlib
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class Role(str, enum.Enum):
    CUSTOMER = "CUSTOMER"
    AGENT = "AGENT"
    ADMIN = "ADMIN"


class OrderType(str, enum.Enum):
    B2B = "B2B"
    B2C = "B2C"


class PaymentType(str, enum.Enum):
    PREPAID = "PREPAID"
    COD = "COD"


class RateScope(str, enum.Enum):
    INTRA = "INTRA"   # pickup zone == drop zone
    INTER = "INTER"   # different zones


class OrderStatus(str, enum.Enum):
    CREATED = "CREATED"
    ASSIGNED = "ASSIGNED"
    PICKED_UP = "PICKED_UP"
    IN_TRANSIT = "IN_TRANSIT"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    RESCHEDULED = "RESCHEDULED"
    CANCELLED = "CANCELLED"


class NotificationChannel(str, enum.Enum):
    EMAIL = "EMAIL"
    SMS = "SMS"


class NotificationStatus(str, enum.Enum):
    SENT = "SENT"
    SIMULATED = "SIMULATED"   # provider not configured; payload still recorded
    FAILED = "FAILED"


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(180), unique=True, index=True, nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.CUSTOMER, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    agent_profile: Mapped["AgentProfile | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class AgentProfile(Base):
    """Availability + location model for a delivery agent."""

    __tablename__ = "agent_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    home_zone_id: Mapped[int | None] = mapped_column(ForeignKey("zones.id"))
    vehicle_type: Mapped[str] = mapped_column(String(40), default="BIKE")
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    max_active_orders: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    active_orders: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_lat: Mapped[float | None] = mapped_column(Float)
    current_lng: Mapped[float | None] = mapped_column(Float)
    location_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="agent_profile")
    home_zone: Mapped["Zone | None"] = relationship()

    @property
    def has_capacity(self) -> bool:
        return self.active_orders < self.max_active_orders


# --------------------------------------------------------------------------- #
# Geography
# --------------------------------------------------------------------------- #
class Zone(Base):
    __tablename__ = "zones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    centroid_lat: Mapped[float | None] = mapped_column(Float)
    centroid_lng: Mapped[float | None] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    areas: Mapped[list["Area"]] = relationship(back_populates="zone", cascade="all, delete-orphan")


class Area(Base):
    """A serviceable pincode mapped to exactly one zone."""

    __tablename__ = "areas"
    __table_args__ = (UniqueConstraint("pincode", name="uq_area_pincode"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pincode: Mapped[str] = mapped_column(String(12), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    city: Mapped[str | None] = mapped_column(String(80))
    state: Mapped[str | None] = mapped_column(String(80))
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id", ondelete="CASCADE"), index=True)
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    is_serviceable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    zone: Mapped[Zone] = relationship(back_populates="areas")


# --------------------------------------------------------------------------- #
# Pricing configuration
# --------------------------------------------------------------------------- #
class RateCard(Base):
    """Slab pricing for one (order_type, scope) combination.

    Lookup precedence, most specific first:
      1. exact lane   (from_zone, to_zone)
      2. origin lane  (from_zone, NULL)
      3. default      (NULL, NULL)
    """

    __tablename__ = "rate_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    order_type: Mapped[OrderType] = mapped_column(Enum(OrderType), nullable=False, index=True)
    scope: Mapped[RateScope] = mapped_column(Enum(RateScope), nullable=False, index=True)
    from_zone_id: Mapped[int | None] = mapped_column(ForeignKey("zones.id", ondelete="CASCADE"))
    to_zone_id: Mapped[int | None] = mapped_column(ForeignKey("zones.id", ondelete="CASCADE"))

    base_weight_kg: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    base_price: Mapped[float] = mapped_column(Float, nullable=False)
    increment_weight_kg: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    increment_price: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    min_charge: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    fuel_surcharge_pct: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    from_zone: Mapped[Zone | None] = relationship(foreign_keys=[from_zone_id])
    to_zone: Mapped[Zone | None] = relationship(foreign_keys=[to_zone_id])

    @property
    def specificity(self) -> int:
        return (2 if self.from_zone_id else 0) + (1 if self.to_zone_id else 0)


class CodRule(Base):
    """COD surcharge per order type: max(flat, pct of freight), clamped."""

    __tablename__ = "cod_rules"
    __table_args__ = (UniqueConstraint("order_type", name="uq_cod_order_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_type: Mapped[OrderType] = mapped_column(Enum(OrderType), nullable=False)
    flat_fee: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    percent_of_freight: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    min_fee: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    max_fee: Mapped[float | None] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class SystemSetting(Base):
    """Admin-tunable engine knobs (volumetric divisor, rounding step, ...)."""

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# --------------------------------------------------------------------------- #
# Orders
# --------------------------------------------------------------------------- #
class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_code: Mapped[str] = mapped_column(String(24), unique=True, index=True, nullable=False)

    customer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)

    # pickup
    pickup_contact: Mapped[str | None] = mapped_column(String(120))
    pickup_phone: Mapped[str | None] = mapped_column(String(20))
    pickup_address: Mapped[str] = mapped_column(Text, nullable=False)
    pickup_pincode: Mapped[str] = mapped_column(String(12), nullable=False)
    pickup_zone_id: Mapped[int | None] = mapped_column(ForeignKey("zones.id"), index=True)
    pickup_lat: Mapped[float | None] = mapped_column(Float)
    pickup_lng: Mapped[float | None] = mapped_column(Float)

    # drop
    drop_contact: Mapped[str | None] = mapped_column(String(120))
    drop_phone: Mapped[str | None] = mapped_column(String(20))
    drop_address: Mapped[str] = mapped_column(Text, nullable=False)
    drop_pincode: Mapped[str] = mapped_column(String(12), nullable=False)
    drop_zone_id: Mapped[int | None] = mapped_column(ForeignKey("zones.id"), index=True)
    drop_lat: Mapped[float | None] = mapped_column(Float)
    drop_lng: Mapped[float | None] = mapped_column(Float)

    # package
    length_cm: Mapped[float] = mapped_column(Float, nullable=False)
    breadth_cm: Mapped[float] = mapped_column(Float, nullable=False)
    height_cm: Mapped[float] = mapped_column(Float, nullable=False)
    actual_weight_kg: Mapped[float] = mapped_column(Float, nullable=False)
    volumetric_weight_kg: Mapped[float] = mapped_column(Float, nullable=False)
    billable_weight_kg: Mapped[float] = mapped_column(Float, nullable=False)
    weight_basis: Mapped[str] = mapped_column(String(20), default="ACTUAL")

    # commercials
    order_type: Mapped[OrderType] = mapped_column(Enum(OrderType), nullable=False)
    payment_type: Mapped[PaymentType] = mapped_column(Enum(PaymentType), nullable=False)
    rate_scope: Mapped[RateScope | None] = mapped_column(Enum(RateScope))
    rate_card_id: Mapped[int | None] = mapped_column(ForeignKey("rate_cards.id"))
    freight_charge: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    fuel_surcharge: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    cod_surcharge: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_charge: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    charge_breakdown: Mapped[str | None] = mapped_column(Text)  # JSON snapshot at quote time

    # lifecycle
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), default=OrderStatus.CREATED, index=True)
    scheduled_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    package_description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    customer: Mapped[User] = relationship(foreign_keys=[customer_id])
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])
    agent: Mapped[User | None] = relationship(foreign_keys=[agent_id])
    pickup_zone: Mapped[Zone | None] = relationship(foreign_keys=[pickup_zone_id])
    drop_zone: Mapped[Zone | None] = relationship(foreign_keys=[drop_zone_id])
    rate_card: Mapped[RateCard | None] = relationship()
    events: Mapped[list["TrackingEvent"]] = relationship(
        back_populates="order", order_by="TrackingEvent.id"
    )
    reschedules: Mapped[list["RescheduleRequest"]] = relationship(back_populates="order")


class TrackingEvent(Base):
    """Append-only checkpoint. See module docstring for the immutability guarantee."""

    __tablename__ = "tracking_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), nullable=False)
    previous_status: Mapped[OrderStatus | None] = mapped_column(Enum(OrderStatus))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    actor_role: Mapped[Role | None] = mapped_column(Enum(Role))
    actor_name: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(Text)
    location_text: Mapped[str | None] = mapped_column(String(180))
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    is_override: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    prev_hash: Mapped[str | None] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    order: Mapped[Order] = relationship(back_populates="events")

    def compute_hash(self) -> str:
        # SQLite hands back naive datetimes, Postgres returns aware ones. Normalise
        # to a UTC epoch string so the chain verifies identically on both.
        ts = self.created_at or utcnow()
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        payload = "|".join(
            [
                str(self.order_id),
                self.status.value if isinstance(self.status, OrderStatus) else str(self.status),
                str(self.actor_id or ""),
                (self.note or ""),
                f"{ts.astimezone(timezone.utc).timestamp():.6f}",
                self.prev_hash or "GENESIS",
            ]
        )
        return hashlib.sha256(payload.encode()).hexdigest()


class ImmutableRecordError(Exception):
    """Raised when something tries to rewrite delivery history."""


@event.listens_for(TrackingEvent, "before_update")
def _block_tracking_update(mapper, connection, target):  # pragma: no cover - guard
    raise ImmutableRecordError("Tracking events are append-only and cannot be modified.")


@event.listens_for(TrackingEvent, "before_delete")
def _block_tracking_delete(mapper, connection, target):  # pragma: no cover - guard
    raise ImmutableRecordError("Tracking events are append-only and cannot be deleted.")


class RescheduleRequest(Base):
    __tablename__ = "reschedule_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    requested_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    requested_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    previous_agent_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    new_agent_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    attempt_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    order: Mapped[Order] = relationship(back_populates="reschedules")


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    channel: Mapped[NotificationChannel] = mapped_column(Enum(NotificationChannel), nullable=False)
    recipient: Mapped[str] = mapped_column(String(180), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[NotificationStatus] = mapped_column(Enum(NotificationStatus), nullable=False)
    provider_response: Mapped[str | None] = mapped_column(Text)
    trigger_status: Mapped[OrderStatus | None] = mapped_column(Enum(OrderStatus))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
