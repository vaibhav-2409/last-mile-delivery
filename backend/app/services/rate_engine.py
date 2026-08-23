"""Rate calculation engine.

Pipeline
--------
    pincodes -> zones -> scope (INTRA/INTER)
    dimensions -> volumetric weight -> billable weight (higher of the two, rounded up)
    (order_type, scope, lane) -> rate card -> freight -> fuel surcharge
    payment_type == COD -> COD rule for that order type -> surcharge
    total = freight + fuel + cod

Every number the engine uses comes from the database: rate cards, COD rules and
the two engine knobs (`volumetric_divisor`, `billable_rounding_step`) in
system_settings. There are no pricing constants in this module.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import CodRule, OrderType, PaymentType, RateCard, RateScope, SystemSetting, Zone
from .zones import detect_zone

DEFAULT_SETTINGS = {
    "volumetric_divisor": ("5000", "Divisor for L×B×H cm to volumetric kg"),
    "billable_rounding_step": ("0.5", "Billable weight is rounded up to this step (kg)"),
}


class RateConfigError(Exception):
    """No rate card / COD rule configured for the requested combination."""


# --------------------------------------------------------------------------- #
# Settings helpers
# --------------------------------------------------------------------------- #
def get_setting_float(db: Session, key: str) -> float:
    row = db.get(SystemSetting, key)
    if row is not None:
        try:
            return float(row.value)
        except ValueError:
            pass
    return float(DEFAULT_SETTINGS[key][0])


# --------------------------------------------------------------------------- #
# Weight
# --------------------------------------------------------------------------- #
def volumetric_weight(length: float, breadth: float, height: float, divisor: float) -> float:
    if divisor <= 0:
        raise RateConfigError("volumetric_divisor must be greater than zero.")
    return round((length * breadth * height) / divisor, 3)


def round_up_to_step(value: float, step: float) -> float:
    if step <= 0:
        return round(value, 3)
    # 1e-9 guard so 2.0 does not creep to 2.5 through float noise
    return round(math.ceil((value - 1e-9) / step) * step, 3)


# --------------------------------------------------------------------------- #
# Rate card lookup
# --------------------------------------------------------------------------- #
def find_rate_card(
    db: Session, order_type: OrderType, scope: RateScope, from_zone_id: int, to_zone_id: int
) -> RateCard:
    """Most specific active card wins: exact lane > origin lane > zone-agnostic default."""
    cards = db.scalars(
        select(RateCard).where(
            RateCard.order_type == order_type,
            RateCard.scope == scope,
            RateCard.is_active.is_(True),
            or_(RateCard.from_zone_id.is_(None), RateCard.from_zone_id == from_zone_id),
            or_(RateCard.to_zone_id.is_(None), RateCard.to_zone_id == to_zone_id),
        )
    ).all()
    if not cards:
        raise RateConfigError(
            f"No active {order_type.value} {scope.value} rate card configured for this lane."
        )
    return sorted(cards, key=lambda c: (c.specificity, c.id), reverse=True)[0]


def compute_freight(card: RateCard, billable_weight: float) -> tuple[float, float]:
    """Returns (freight_before_fuel, fuel_surcharge)."""
    extra = max(0.0, billable_weight - card.base_weight_kg)
    slabs = math.ceil((extra - 1e-9) / card.increment_weight_kg) if extra > 0 else 0
    freight = card.base_price + slabs * card.increment_price
    freight = max(freight, card.min_charge)
    fuel = round(freight * (card.fuel_surcharge_pct / 100.0), 2)
    return round(freight, 2), fuel


def compute_cod(db: Session, order_type: OrderType, freight_total: float) -> tuple[float, CodRule | None]:
    rule = db.scalar(
        select(CodRule).where(CodRule.order_type == order_type, CodRule.is_active.is_(True))
    )
    if rule is None:
        raise RateConfigError(f"No active COD rule configured for {order_type.value} orders.")
    pct_component = freight_total * (rule.percent_of_freight / 100.0)
    fee = max(rule.flat_fee, pct_component)
    fee = max(fee, rule.min_fee)
    if rule.max_fee is not None:
        fee = min(fee, rule.max_fee)
    return round(fee, 2), rule


# --------------------------------------------------------------------------- #
# Quote
# --------------------------------------------------------------------------- #
@dataclass
class QuoteLine:
    label: str
    detail: str
    amount: float


@dataclass
class Quote:
    pickup_zone: Zone
    drop_zone: Zone
    scope: RateScope
    order_type: OrderType
    payment_type: PaymentType
    actual_weight_kg: float
    volumetric_weight_kg: float
    billable_weight_kg: float
    weight_basis: str
    volumetric_divisor: float
    rate_card: RateCard
    freight_charge: float
    fuel_surcharge: float
    cod_surcharge: float
    total_charge: float
    lines: list[QuoteLine] = field(default_factory=list)

    def to_breakdown_dict(self) -> dict:
        return {
            "pickup_zone": self.pickup_zone.code,
            "drop_zone": self.drop_zone.code,
            "scope": self.scope.value,
            "order_type": self.order_type.value,
            "payment_type": self.payment_type.value,
            "actual_weight_kg": self.actual_weight_kg,
            "volumetric_weight_kg": self.volumetric_weight_kg,
            "billable_weight_kg": self.billable_weight_kg,
            "weight_basis": self.weight_basis,
            "volumetric_divisor": self.volumetric_divisor,
            "rate_card": {"id": self.rate_card.id, "name": self.rate_card.name},
            "freight_charge": self.freight_charge,
            "fuel_surcharge": self.fuel_surcharge,
            "cod_surcharge": self.cod_surcharge,
            "total_charge": self.total_charge,
            "lines": [asdict(line) for line in self.lines],
        }


def build_quote(
    db: Session,
    *,
    pickup_pincode: str,
    drop_pincode: str,
    length_cm: float,
    breadth_cm: float,
    height_cm: float,
    actual_weight_kg: float,
    order_type: OrderType,
    payment_type: PaymentType,
) -> Quote:
    pickup_zone, _ = detect_zone(db, pickup_pincode)
    drop_zone, _ = detect_zone(db, drop_pincode)
    scope = RateScope.INTRA if pickup_zone.id == drop_zone.id else RateScope.INTER

    divisor = get_setting_float(db, "volumetric_divisor")
    step = get_setting_float(db, "billable_rounding_step")

    vol = volumetric_weight(length_cm, breadth_cm, height_cm, divisor)
    raw_billable = max(actual_weight_kg, vol)
    basis = "VOLUMETRIC" if vol > actual_weight_kg else "ACTUAL"
    billable = round_up_to_step(raw_billable, step)

    card = find_rate_card(db, order_type, scope, pickup_zone.id, drop_zone.id)
    freight, fuel = compute_freight(card, billable)

    cod = 0.0
    cod_rule = None
    if payment_type == PaymentType.COD:
        cod, cod_rule = compute_cod(db, order_type, freight + fuel)

    total = round(freight + fuel + cod, 2)

    lines = [
        QuoteLine(
            "Freight",
            f"{card.name} · {billable:g} kg billable "
            f"(base {card.base_weight_kg:g} kg @ ₹{card.base_price:g}"
            + (
                f" + ₹{card.increment_price:g} per {card.increment_weight_kg:g} kg)"
                if billable > card.base_weight_kg
                else ")"
            ),
            freight,
        )
    ]
    if fuel:
        lines.append(QuoteLine("Fuel surcharge", f"{card.fuel_surcharge_pct:g}% of freight", fuel))
    if cod and cod_rule:
        detail = f"{order_type.value} COD: higher of ₹{cod_rule.flat_fee:g} or {cod_rule.percent_of_freight:g}%"
        lines.append(QuoteLine("COD handling", detail, cod))

    return Quote(
        pickup_zone=pickup_zone,
        drop_zone=drop_zone,
        scope=scope,
        order_type=order_type,
        payment_type=payment_type,
        actual_weight_kg=round(actual_weight_kg, 3),
        volumetric_weight_kg=vol,
        billable_weight_kg=billable,
        weight_basis=basis,
        volumetric_divisor=divisor,
        rate_card=card,
        freight_charge=freight,
        fuel_surcharge=fuel,
        cod_surcharge=cod,
        total_charge=total,
        lines=lines,
    )
