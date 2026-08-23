from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from .models import (
    NotificationChannel,
    NotificationStatus,
    OrderStatus,
    OrderType,
    PaymentType,
    RateScope,
    Role,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
class RegisterRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=20)
    password: str = Field(min_length=6, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(ORMModel):
    id: int
    name: str
    email: EmailStr
    phone: str | None = None
    role: Role
    is_active: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# --------------------------------------------------------------------------- #
# Zones / areas
# --------------------------------------------------------------------------- #
class ZoneCreate(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str
    description: str | None = None
    centroid_lat: float | None = None
    centroid_lng: float | None = None


class ZoneUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    centroid_lat: float | None = None
    centroid_lng: float | None = None
    is_active: bool | None = None


class ZoneOut(ORMModel):
    id: int
    code: str
    name: str
    description: str | None = None
    centroid_lat: float | None = None
    centroid_lng: float | None = None
    is_active: bool


class AreaCreate(BaseModel):
    pincode: str = Field(min_length=3, max_length=12)
    name: str
    city: str | None = None
    state: str | None = None
    zone_id: int
    lat: float | None = None
    lng: float | None = None


class AreaUpdate(BaseModel):
    name: str | None = None
    city: str | None = None
    state: str | None = None
    zone_id: int | None = None
    lat: float | None = None
    lng: float | None = None
    is_serviceable: bool | None = None


class AreaOut(ORMModel):
    id: int
    pincode: str
    name: str
    city: str | None = None
    state: str | None = None
    zone_id: int
    zone: ZoneOut | None = None
    lat: float | None = None
    lng: float | None = None
    is_serviceable: bool


# --------------------------------------------------------------------------- #
# Rate configuration
# --------------------------------------------------------------------------- #
class RateCardCreate(BaseModel):
    name: str
    order_type: OrderType
    scope: RateScope
    from_zone_id: int | None = None
    to_zone_id: int | None = None
    base_weight_kg: float = Field(default=1.0, gt=0)
    base_price: float = Field(ge=0)
    increment_weight_kg: float = Field(default=0.5, gt=0)
    increment_price: float = Field(default=0.0, ge=0)
    min_charge: float = Field(default=0.0, ge=0)
    fuel_surcharge_pct: float = Field(default=0.0, ge=0, le=100)


class RateCardUpdate(BaseModel):
    name: str | None = None
    base_weight_kg: float | None = Field(default=None, gt=0)
    base_price: float | None = Field(default=None, ge=0)
    increment_weight_kg: float | None = Field(default=None, gt=0)
    increment_price: float | None = Field(default=None, ge=0)
    min_charge: float | None = Field(default=None, ge=0)
    fuel_surcharge_pct: float | None = Field(default=None, ge=0, le=100)
    is_active: bool | None = None


class RateCardOut(ORMModel):
    id: int
    name: str
    order_type: OrderType
    scope: RateScope
    from_zone_id: int | None = None
    to_zone_id: int | None = None
    from_zone: ZoneOut | None = None
    to_zone: ZoneOut | None = None
    base_weight_kg: float
    base_price: float
    increment_weight_kg: float
    increment_price: float
    min_charge: float
    fuel_surcharge_pct: float
    is_active: bool


class CodRuleUpsert(BaseModel):
    order_type: OrderType
    flat_fee: float = Field(default=0.0, ge=0)
    percent_of_freight: float = Field(default=0.0, ge=0, le=100)
    min_fee: float = Field(default=0.0, ge=0)
    max_fee: float | None = Field(default=None, ge=0)
    is_active: bool = True


class CodRuleOut(ORMModel):
    id: int
    order_type: OrderType
    flat_fee: float
    percent_of_freight: float
    min_fee: float
    max_fee: float | None = None
    is_active: bool


class SettingUpsert(BaseModel):
    key: str
    value: str
    description: str | None = None


class SettingOut(ORMModel):
    key: str
    value: str
    description: str | None = None


# --------------------------------------------------------------------------- #
# Quote / orders
# --------------------------------------------------------------------------- #
class QuoteRequest(BaseModel):
    pickup_pincode: str
    drop_pincode: str
    length_cm: float = Field(gt=0)
    breadth_cm: float = Field(gt=0)
    height_cm: float = Field(gt=0)
    actual_weight_kg: float = Field(gt=0)
    order_type: OrderType
    payment_type: PaymentType


class QuoteLine(BaseModel):
    label: str
    detail: str
    amount: float


class QuoteResponse(BaseModel):
    pickup_zone: ZoneOut
    drop_zone: ZoneOut
    scope: RateScope
    order_type: OrderType
    payment_type: PaymentType
    volumetric_weight_kg: float
    actual_weight_kg: float
    billable_weight_kg: float
    weight_basis: str
    volumetric_divisor: float
    rate_card_id: int
    rate_card_name: str
    freight_charge: float
    fuel_surcharge: float
    cod_surcharge: float
    total_charge: float
    lines: list[QuoteLine]


class OrderCreate(BaseModel):
    customer_id: int | None = None  # admin creating on behalf of a customer
    pickup_contact: str | None = None
    pickup_phone: str | None = None
    pickup_address: str
    pickup_pincode: str
    pickup_lat: float | None = None
    pickup_lng: float | None = None
    drop_contact: str | None = None
    drop_phone: str | None = None
    drop_address: str
    drop_pincode: str
    drop_lat: float | None = None
    drop_lng: float | None = None
    length_cm: float = Field(gt=0)
    breadth_cm: float = Field(gt=0)
    height_cm: float = Field(gt=0)
    actual_weight_kg: float = Field(gt=0)
    order_type: OrderType
    payment_type: PaymentType
    package_description: str | None = None
    scheduled_date: datetime | None = None
    auto_assign: bool = False


class TrackingEventOut(ORMModel):
    id: int
    status: OrderStatus
    previous_status: OrderStatus | None = None
    actor_name: str | None = None
    actor_role: Role | None = None
    note: str | None = None
    location_text: str | None = None
    is_override: bool
    created_at: datetime
    event_hash: str
    prev_hash: str | None = None


class OrderSummary(ORMModel):
    id: int
    order_code: str
    status: OrderStatus
    order_type: OrderType
    payment_type: PaymentType
    pickup_pincode: str
    drop_pincode: str
    pickup_zone: ZoneOut | None = None
    drop_zone: ZoneOut | None = None
    billable_weight_kg: float
    total_charge: float
    agent_id: int | None = None
    agent: UserOut | None = None
    customer: UserOut | None = None
    scheduled_date: datetime | None = None
    delivery_attempts: int
    created_at: datetime


class OrderDetail(OrderSummary):
    pickup_contact: str | None = None
    pickup_phone: str | None = None
    pickup_address: str
    drop_contact: str | None = None
    drop_phone: str | None = None
    drop_address: str
    length_cm: float
    breadth_cm: float
    height_cm: float
    actual_weight_kg: float
    volumetric_weight_kg: float
    weight_basis: str
    rate_scope: RateScope | None = None
    rate_card_id: int | None = None
    freight_charge: float
    fuel_surcharge: float
    cod_surcharge: float
    charge_breakdown: str | None = None
    package_description: str | None = None
    failure_reason: str | None = None
    delivered_at: datetime | None = None
    events: list[TrackingEventOut] = []


class StatusUpdateRequest(BaseModel):
    status: OrderStatus
    note: str | None = None
    location_text: str | None = None
    lat: float | None = None
    lng: float | None = None
    failure_reason: str | None = None


class AssignRequest(BaseModel):
    agent_id: int


class RescheduleRequestIn(BaseModel):
    requested_date: datetime
    reason: str | None = None


class AgentProfileOut(ORMModel):
    id: int
    user_id: int
    home_zone_id: int | None = None
    home_zone: ZoneOut | None = None
    vehicle_type: str
    is_available: bool
    max_active_orders: int
    active_orders: int
    current_lat: float | None = None
    current_lng: float | None = None
    user: UserOut | None = None


class AgentCreate(BaseModel):
    name: str
    email: EmailStr
    phone: str | None = None
    password: str = Field(min_length=6)
    home_zone_id: int | None = None
    vehicle_type: str = "BIKE"
    max_active_orders: int = Field(default=5, gt=0)
    current_lat: float | None = None
    current_lng: float | None = None


class AgentUpdate(BaseModel):
    home_zone_id: int | None = None
    vehicle_type: str | None = None
    is_available: bool | None = None
    max_active_orders: int | None = Field(default=None, gt=0)
    current_lat: float | None = None
    current_lng: float | None = None


class NotificationOut(ORMModel):
    id: int
    order_id: int | None = None
    channel: NotificationChannel
    recipient: str
    subject: str | None = None
    body: str
    status: NotificationStatus
    trigger_status: OrderStatus | None = None
    created_at: datetime


class MessageResponse(BaseModel):
    message: str


class IntegrityReport(BaseModel):
    order_code: str
    events: int
    intact: bool
    broken_at: int | None = None
