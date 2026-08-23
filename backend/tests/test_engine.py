"""Unit tests for the three pieces the brief calls out as evaluation focus:
the rate calculation engine, the auto-assignment ranking, and the immutable
status lifecycle.

Run with:  pytest -q   (from the backend/ directory)
"""
from __future__ import annotations

import os
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import Base  # noqa: E402
from app.models import (  # noqa: E402
    AgentProfile,
    Area,
    CodRule,
    ImmutableRecordError,
    Order,
    OrderStatus,
    OrderType,
    PaymentType,
    RateCard,
    RateScope,
    Role,
    SystemSetting,
    User,
    Zone,
)
from app.services import assignment, lifecycle  # noqa: E402
from app.services.rate_engine import (  # noqa: E402
    RateConfigError,
    build_quote,
    round_up_to_step,
    volumetric_weight,
)
from app.services.zones import ZoneNotFound, detect_zone  # noqa: E402


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    north = Zone(code="N", name="North", centroid_lat=13.10, centroid_lng=80.28)
    south = Zone(code="S", name="South", centroid_lat=12.95, centroid_lng=80.14)
    session.add_all([north, south])
    session.flush()

    session.add_all(
        [
            Area(pincode="600001", name="Parrys", zone_id=north.id, lat=13.09, lng=80.28),
            Area(pincode="600011", name="Perambur", zone_id=north.id, lat=13.11, lng=80.23),
            Area(pincode="600020", name="Adyar", zone_id=south.id, lat=13.00, lng=80.25),
        ]
    )
    session.add_all(
        [
            SystemSetting(key="volumetric_divisor", value="5000"),
            SystemSetting(key="billable_rounding_step", value="0.5"),
            RateCard(
                name="B2C intra", order_type=OrderType.B2C, scope=RateScope.INTRA,
                base_weight_kg=1, base_price=45, increment_weight_kg=0.5,
                increment_price=18, min_charge=45,
            ),
            RateCard(
                name="B2C inter", order_type=OrderType.B2C, scope=RateScope.INTER,
                base_weight_kg=1, base_price=75, increment_weight_kg=0.5,
                increment_price=28, min_charge=75,
            ),
            RateCard(
                name="B2B intra", order_type=OrderType.B2B, scope=RateScope.INTRA,
                base_weight_kg=5, base_price=140, increment_weight_kg=1,
                increment_price=22, min_charge=140,
            ),
            RateCard(
                name="B2B inter", order_type=OrderType.B2B, scope=RateScope.INTER,
                base_weight_kg=5, base_price=230, increment_weight_kg=1,
                increment_price=34, min_charge=230,
            ),
            CodRule(order_type=OrderType.B2C, flat_fee=35, percent_of_freight=2, min_fee=35, max_fee=250),
            CodRule(order_type=OrderType.B2B, flat_fee=60, percent_of_freight=1.5, min_fee=60, max_fee=750),
        ]
    )
    session.commit()
    yield session
    session.close()


def q(db, **over):
    args = dict(
        pickup_pincode="600001",
        drop_pincode="600020",
        length_cm=40,
        breadth_cm=30,
        height_cm=20,
        actual_weight_kg=2.0,
        order_type=OrderType.B2C,
        payment_type=PaymentType.PREPAID,
    )
    args.update(over)
    return build_quote(db, **args)


# --------------------------------------------------------------------------- #
# Zone detection
# --------------------------------------------------------------------------- #
def test_exact_pincode_resolves_to_zone(db):
    zone, area = detect_zone(db, "600020")
    assert zone.code == "S" and area.name == "Adyar"


def test_unmapped_pincode_falls_back_to_longest_prefix(db):
    zone, area = detect_zone(db, "600015")  # shares 6000 with north/south areas
    assert zone is not None and area is None


def test_unserviceable_pincode_raises(db):
    with pytest.raises(ZoneNotFound):
        detect_zone(db, "999999")


# --------------------------------------------------------------------------- #
# Weight
# --------------------------------------------------------------------------- #
def test_volumetric_weight_uses_configured_divisor():
    assert volumetric_weight(40, 30, 20, 5000) == 4.8


def test_bills_on_higher_of_actual_vs_volumetric(db):
    heavy = q(db, actual_weight_kg=9.0)          # actual wins
    bulky = q(db, actual_weight_kg=2.0)          # volumetric wins
    assert heavy.weight_basis == "ACTUAL" and heavy.billable_weight_kg == 9.0
    assert bulky.weight_basis == "VOLUMETRIC" and bulky.billable_weight_kg == 5.0


def test_billable_weight_rounds_up_to_step():
    assert round_up_to_step(4.8, 0.5) == 5.0
    assert round_up_to_step(2.0, 0.5) == 2.0     # exact values must not creep up
    assert round_up_to_step(2.01, 0.5) == 2.5


def test_divisor_is_admin_configurable(db):
    db.get(SystemSetting, "volumetric_divisor").value = "6000"
    db.commit()
    assert q(db).volumetric_weight_kg == 4.0


# --------------------------------------------------------------------------- #
# Rate cards
# --------------------------------------------------------------------------- #
def test_intra_zone_uses_intra_card(db):
    quote = q(db, drop_pincode="600011")
    assert quote.scope == RateScope.INTRA and quote.rate_card.name == "B2C intra"


def test_inter_zone_uses_inter_card(db):
    assert q(db).scope == RateScope.INTER


def test_b2b_and_b2c_price_differently(db):
    b2c = q(db, actual_weight_kg=10, length_cm=1, breadth_cm=1, height_cm=1)
    b2b = q(db, actual_weight_kg=10, length_cm=1, breadth_cm=1, height_cm=1, order_type=OrderType.B2B)
    # B2C: 75 + ceil(9/0.5)*28 = 579 ; B2B: 230 + ceil(5/1)*34 = 400
    assert b2c.freight_charge == 579.0
    assert b2b.freight_charge == 400.0


def test_slab_pricing_is_exact(db):
    quote = q(db)  # billable 5.0 kg on the inter card
    assert quote.freight_charge == 75 + 8 * 28


def test_lane_specific_card_beats_the_default(db):
    north = db.query(Zone).filter_by(code="N").one()
    south = db.query(Zone).filter_by(code="S").one()
    db.add(
        RateCard(
            name="N->S promo", order_type=OrderType.B2C, scope=RateScope.INTER,
            from_zone_id=north.id, to_zone_id=south.id,
            base_weight_kg=1, base_price=50, increment_weight_kg=0.5,
            increment_price=10, min_charge=50,
        )
    )
    db.commit()
    quote = q(db)
    assert quote.rate_card.name == "N->S promo"
    assert quote.freight_charge == 50 + 8 * 10


def test_min_charge_floor_applies(db):
    card = db.query(RateCard).filter_by(name="B2C inter").one()
    card.min_charge = 500
    db.commit()
    assert q(db).freight_charge == 500


def test_missing_rate_card_is_a_config_error(db):
    for card in db.query(RateCard).all():
        card.is_active = False
    db.commit()
    with pytest.raises(RateConfigError):
        q(db)


# --------------------------------------------------------------------------- #
# COD
# --------------------------------------------------------------------------- #
def test_prepaid_orders_carry_no_cod_surcharge(db):
    assert q(db).cod_surcharge == 0.0


def test_cod_takes_the_higher_of_flat_or_percent(db):
    small = q(db, payment_type=PaymentType.COD)                      # flat 35 wins
    assert small.cod_surcharge == 35.0
    big = q(db, payment_type=PaymentType.COD, actual_weight_kg=60,
            length_cm=1, breadth_cm=1, height_cm=1)                  # 2% wins
    assert big.cod_surcharge == pytest.approx(big.freight_charge * 0.02, rel=1e-3)


def test_cod_respects_the_configured_cap(db):
    rule = db.query(CodRule).filter_by(order_type=OrderType.B2C).one()
    rule.max_fee = 100
    db.commit()
    quote = q(db, payment_type=PaymentType.COD, actual_weight_kg=200,
              length_cm=1, breadth_cm=1, height_cm=1)
    assert quote.cod_surcharge == 100


def test_total_is_freight_plus_fuel_plus_cod(db):
    quote = q(db, payment_type=PaymentType.COD)
    assert quote.total_charge == round(
        quote.freight_charge + quote.fuel_surcharge + quote.cod_surcharge, 2
    )


# --------------------------------------------------------------------------- #
# Auto-assignment
# --------------------------------------------------------------------------- #
@pytest.fixture()
def staffed(db):
    north = db.query(Zone).filter_by(code="N").one()
    south = db.query(Zone).filter_by(code="S").one()
    made = {}
    for name, zone, lat, lng, load in [
        ("near_north", north, 13.10, 80.28, 0),
        ("busy_north", north, 13.10, 80.28, 5),
        ("far_south", south, 12.95, 80.14, 0),
    ]:
        user = User(name=name, email=f"{name}@t.dev", password_hash="x", role=Role.AGENT)
        db.add(user)
        db.flush()
        db.add(
            AgentProfile(
                user_id=user.id, home_zone_id=zone.id, current_lat=lat, current_lng=lng,
                active_orders=load, max_active_orders=5,
            )
        )
        made[name] = user
    order = Order(
        order_code="T1", customer_id=1, created_by_id=1,
        pickup_address="a", pickup_pincode="600001", pickup_zone_id=north.id,
        pickup_lat=13.09, pickup_lng=80.28,
        drop_address="b", drop_pincode="600020", drop_zone_id=south.id,
        length_cm=1, breadth_cm=1, height_cm=1,
        actual_weight_kg=1, volumetric_weight_kg=1, billable_weight_kg=1,
        order_type=OrderType.B2C, payment_type=PaymentType.PREPAID,
    )
    db.add(order)
    db.commit()
    return db, order, made


def test_nearest_available_agent_in_pickup_zone_wins(staffed):
    db, order, made = staffed
    pick = assignment.find_nearest_agent(db, order)
    assert pick.profile.user_id == made["near_north"].id


def test_agents_at_capacity_are_skipped(staffed):
    db, order, made = staffed
    db.query(AgentProfile).filter_by(user_id=made["near_north"].id).one().active_orders = 5
    db.commit()
    # busy_north is also full, so the only candidate left is the far one
    assert assignment.find_nearest_agent(db, order).profile.user_id == made["far_south"].id


def test_unavailable_agents_are_skipped(staffed):
    db, order, made = staffed
    db.query(AgentProfile).filter_by(user_id=made["near_north"].id).one().is_available = False
    db.commit()
    assert assignment.find_nearest_agent(db, order).profile.user_id != made["near_north"].id


def test_reassignment_prefers_someone_other_than_the_failed_agent(staffed):
    db, order, made = staffed
    pick = assignment.find_nearest_agent(db, order, exclude_agent_id=made["near_north"].id)
    assert pick.profile.user_id != made["near_north"].id


def test_no_free_agent_returns_none(staffed):
    db, order, _ = staffed
    for profile in db.query(AgentProfile).all():
        profile.is_available = False
    db.commit()
    assert assignment.find_nearest_agent(db, order) is None


# --------------------------------------------------------------------------- #
# Lifecycle + immutable history
# --------------------------------------------------------------------------- #
def test_valid_transitions_are_allowed():
    assert lifecycle.can_transition(OrderStatus.ASSIGNED, OrderStatus.PICKED_UP)
    assert lifecycle.can_transition(OrderStatus.FAILED, OrderStatus.RESCHEDULED)


def test_illegal_transitions_are_rejected():
    assert not lifecycle.can_transition(OrderStatus.CREATED, OrderStatus.DELIVERED)
    assert not lifecycle.can_transition(OrderStatus.DELIVERED, OrderStatus.IN_TRANSIT)


def test_apply_status_refuses_an_illegal_jump(staffed):
    db, order, _ = staffed
    with pytest.raises(lifecycle.InvalidTransition):
        lifecycle.apply_status(db, order, status=OrderStatus.DELIVERED, actor=None)


def test_admin_override_bypasses_the_map_and_is_flagged(staffed):
    db, order, _ = staffed
    event = lifecycle.apply_status(
        db, order, status=OrderStatus.DELIVERED, actor=None, override=True
    )
    assert event.is_override and order.status == OrderStatus.DELIVERED


def test_tracking_events_cannot_be_updated(staffed):
    db, order, _ = staffed
    event = lifecycle.record_event(db, order, status=OrderStatus.ASSIGNED, actor=None)
    db.commit()
    event.note = "rewriting history"
    with pytest.raises(ImmutableRecordError):
        db.commit()
    db.rollback()


def test_tracking_events_cannot_be_deleted(staffed):
    db, order, _ = staffed
    event = lifecycle.record_event(db, order, status=OrderStatus.ASSIGNED, actor=None)
    db.commit()
    db.delete(event)
    with pytest.raises(ImmutableRecordError):
        db.commit()
    db.rollback()


def test_hash_chain_verifies_and_detects_tampering(staffed):
    db, order, _ = staffed
    lifecycle.record_event(db, order, status=OrderStatus.ASSIGNED, actor=None)
    lifecycle.record_event(db, order, status=OrderStatus.PICKED_UP, actor=None)
    db.commit()
    intact, broken_at, count = lifecycle.verify_chain(db, order)
    assert intact and broken_at is None and count == 2

    # tamper underneath the ORM guard, the way someone with DB access would
    db.execute(
        Order.__table__.metadata.tables["tracking_events"].update().values(note="edited")
    )
    db.commit()
    intact, broken_at, _ = lifecycle.verify_chain(db, order)
    assert not intact and broken_at is not None
