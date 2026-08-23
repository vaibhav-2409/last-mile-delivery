"""Idempotent seed data.

Creates the Chennai-region zone map, a full rate card matrix (INTRA/INTER ×
B2B/B2C), COD rules, engine settings, an admin, four agents and two customers.
Safe to run repeatedly — it only inserts what is missing.
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .database import SessionLocal
from .models import (
    AgentProfile,
    Area,
    CodRule,
    OrderType,
    RateCard,
    RateScope,
    Role,
    SystemSetting,
    User,
    Zone,
)
from .security import hash_password
from .services.rate_engine import DEFAULT_SETTINGS

log = logging.getLogger("lastmile.seed")

ZONES = [
    ("CHN-N", "Chennai North", 13.1067, 80.2897),
    ("CHN-S", "Chennai South", 12.9516, 80.1462),
    ("CHN-W", "Chennai West", 13.0350, 80.1500),
    ("TN-OUT", "Tamil Nadu Outstation", 11.0168, 76.9558),
]

AREAS = [
    ("600001", "Parrys Corner", "CHN-N", 13.0925, 80.2870),
    ("600011", "Perambur", "CHN-N", 13.1100, 80.2340),
    ("600019", "Tiruvottiyur", "CHN-N", 13.1600, 80.3000),
    ("600020", "Adyar", "CHN-S", 13.0067, 80.2570),
    ("600041", "Thiruvanmiyur", "CHN-S", 12.9830, 80.2590),
    ("600100", "Medavakkam", "CHN-S", 12.9180, 80.1920),
    ("600126", "Vengaivasal", "CHN-S", 12.9080, 80.1780),
    ("600026", "Vadapalani", "CHN-W", 13.0500, 80.2120),
    ("600056", "Poonamallee", "CHN-W", 13.0480, 80.0950),
    ("600095", "Maduravoyal", "CHN-W", 13.0650, 80.1650),
    ("641001", "Coimbatore RS Puram", "TN-OUT", 11.0050, 76.9500),
    ("620001", "Trichy Fort", "TN-OUT", 10.8290, 78.6890),
]

# name, order_type, scope, base_kg, base_price, inc_kg, inc_price, min_charge, fuel%
RATE_CARDS = [
    ("B2C Intra-zone standard", OrderType.B2C, RateScope.INTRA, 1.0, 45.0, 0.5, 18.0, 45.0, 4.0),
    ("B2C Inter-zone standard", OrderType.B2C, RateScope.INTER, 1.0, 75.0, 0.5, 28.0, 75.0, 6.0),
    ("B2B Intra-zone contract", OrderType.B2B, RateScope.INTRA, 5.0, 140.0, 1.0, 22.0, 140.0, 4.0),
    ("B2B Inter-zone contract", OrderType.B2B, RateScope.INTER, 5.0, 230.0, 1.0, 34.0, 230.0, 6.0),
]

AGENTS = [
    ("Arun Prakash", "arun.agent@lastmile.dev", "+919000000001", "CHN-N", "BIKE", 13.1100, 80.2500),
    ("Divya Raman", "divya.agent@lastmile.dev", "+919000000002", "CHN-S", "BIKE", 12.9500, 80.2200),
    ("Karthik Vel", "karthik.agent@lastmile.dev", "+919000000003", "CHN-W", "VAN", 13.0500, 80.1400),
    ("Meera Iyer", "meera.agent@lastmile.dev", "+919000000004", "TN-OUT", "TRUCK", 11.0100, 76.9600),
]

CUSTOMERS = [
    ("Rohit Sharma", "rohit@example.com", "+919812345670"),
    ("Anitha Kumar", "anitha@example.com", "+919812345671"),
]

DEMO_PASSWORD = "Passw0rd!"


def seed(db: Session) -> None:
    # --- engine settings ---
    for key, (value, desc) in DEFAULT_SETTINGS.items():
        if db.get(SystemSetting, key) is None:
            db.add(SystemSetting(key=key, value=value, description=desc))

    # --- zones ---
    zone_by_code: dict[str, Zone] = {}
    for code, name, lat, lng in ZONES:
        zone = db.scalar(select(Zone).where(Zone.code == code))
        if zone is None:
            zone = Zone(code=code, name=name, centroid_lat=lat, centroid_lng=lng,
                        description=f"Auto-seeded zone for {name}")
            db.add(zone)
            db.flush()
        zone_by_code[code] = zone

    # --- areas ---
    for pincode, name, zone_code, lat, lng in AREAS:
        if db.scalar(select(Area).where(Area.pincode == pincode)) is None:
            db.add(
                Area(
                    pincode=pincode,
                    name=name,
                    city="Chennai" if zone_code.startswith("CHN") else name.split()[0],
                    state="Tamil Nadu",
                    zone_id=zone_by_code[zone_code].id,
                    lat=lat,
                    lng=lng,
                )
            )

    # --- rate cards (zone-agnostic defaults; admin can add lane overrides) ---
    for name, otype, scope, base_kg, base_p, inc_kg, inc_p, min_c, fuel in RATE_CARDS:
        exists = db.scalar(
            select(RateCard).where(
                RateCard.order_type == otype,
                RateCard.scope == scope,
                RateCard.from_zone_id.is_(None),
                RateCard.to_zone_id.is_(None),
            )
        )
        if exists is None:
            db.add(
                RateCard(
                    name=name,
                    order_type=otype,
                    scope=scope,
                    base_weight_kg=base_kg,
                    base_price=base_p,
                    increment_weight_kg=inc_kg,
                    increment_price=inc_p,
                    min_charge=min_c,
                    fuel_surcharge_pct=fuel,
                )
            )

    # --- COD rules ---
    for otype, flat, pct, min_fee, max_fee in [
        (OrderType.B2C, 35.0, 2.0, 35.0, 250.0),
        (OrderType.B2B, 60.0, 1.5, 60.0, 750.0),
    ]:
        if db.scalar(select(CodRule).where(CodRule.order_type == otype)) is None:
            db.add(
                CodRule(
                    order_type=otype,
                    flat_fee=flat,
                    percent_of_freight=pct,
                    min_fee=min_fee,
                    max_fee=max_fee,
                )
            )

    db.flush()

    # --- admin ---
    if db.scalar(select(User).where(User.email == settings.ADMIN_EMAIL.lower())) is None:
        db.add(
            User(
                name="Operations Admin",
                email=settings.ADMIN_EMAIL.lower(),
                phone="+919800000000",
                password_hash=hash_password(settings.ADMIN_PASSWORD),
                role=Role.ADMIN,
            )
        )

    # --- agents ---
    for name, email, phone, zone_code, vehicle, lat, lng in AGENTS:
        if db.scalar(select(User).where(User.email == email)) is None:
            user = User(
                name=name,
                email=email,
                phone=phone,
                password_hash=hash_password(DEMO_PASSWORD),
                role=Role.AGENT,
            )
            db.add(user)
            db.flush()
            db.add(
                AgentProfile(
                    user_id=user.id,
                    home_zone_id=zone_by_code[zone_code].id,
                    vehicle_type=vehicle,
                    current_lat=lat,
                    current_lng=lng,
                    max_active_orders=5 if vehicle == "BIKE" else 8,
                )
            )

    # --- customers ---
    for name, email, phone in CUSTOMERS:
        if db.scalar(select(User).where(User.email == email)) is None:
            db.add(
                User(
                    name=name,
                    email=email,
                    phone=phone,
                    password_hash=hash_password(DEMO_PASSWORD),
                    role=Role.CUSTOMER,
                )
            )

    db.commit()
    log.info(
        "Seed complete: %s zones, %s areas, %s rate cards",
        db.scalar(select(func.count(Zone.id))),
        db.scalar(select(func.count(Area.id))),
        db.scalar(select(func.count(RateCard.id))),
    )


def run() -> None:
    db = SessionLocal()
    try:
        seed(db)
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
    print("Seeded. Admin:", settings.ADMIN_EMAIL, "/", settings.ADMIN_PASSWORD)
    print("Demo agents & customers password:", DEMO_PASSWORD)
