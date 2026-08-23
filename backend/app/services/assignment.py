"""Agent auto-assignment.

Candidate filter: role AGENT, account active, marked available, and active load
below the agent's capacity.

Ranking (lower score is better) — a lexicographic tuple:
  1. exclusion penalty  – the agent who just failed this order is pushed last
  2. zone match         – agents whose home zone is the pickup zone come first
  3. distance km        – haversine from the agent's last known GPS fix to the
                          pickup point; falls back to zone centroid distance,
                          then to a large constant when neither side has coords
  4. load ratio         – active_orders / max_active_orders, so work spreads out
  5. agent id           – deterministic tie-break, keeps tests stable
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AgentProfile, Order, Role, User
from .zones import haversine_km

UNKNOWN_DISTANCE_KM = 9_999.0


@dataclass
class Candidate:
    profile: AgentProfile
    distance_km: float | None
    zone_match: bool
    load_ratio: float

    @property
    def score(self) -> tuple:
        return (
            0 if self.zone_match else 1,
            self.distance_km if self.distance_km is not None else UNKNOWN_DISTANCE_KM,
            self.load_ratio,
            self.profile.user_id,
        )

    def reason(self) -> str:
        bits = []
        if self.zone_match:
            bits.append("in pickup zone")
        if self.distance_km is not None and self.distance_km < UNKNOWN_DISTANCE_KM:
            bits.append(f"{self.distance_km:.1f} km away")
        bits.append(f"load {self.profile.active_orders}/{self.profile.max_active_orders}")
        return ", ".join(bits)


def _pickup_point(order: Order) -> tuple[float | None, float | None]:
    if order.pickup_lat is not None and order.pickup_lng is not None:
        return order.pickup_lat, order.pickup_lng
    if order.pickup_zone and order.pickup_zone.centroid_lat is not None:
        return order.pickup_zone.centroid_lat, order.pickup_zone.centroid_lng
    return None, None


def _agent_point(profile: AgentProfile) -> tuple[float | None, float | None]:
    if profile.current_lat is not None and profile.current_lng is not None:
        return profile.current_lat, profile.current_lng
    if profile.home_zone and profile.home_zone.centroid_lat is not None:
        return profile.home_zone.centroid_lat, profile.home_zone.centroid_lng
    return None, None


def rank_candidates(db: Session, order: Order, exclude_agent_id: int | None = None) -> list[Candidate]:
    rows = db.execute(
        select(AgentProfile, User)
        .join(User, User.id == AgentProfile.user_id)
        .where(
            User.role == Role.AGENT,
            User.is_active.is_(True),
            AgentProfile.is_available.is_(True),
        )
    ).all()

    p_lat, p_lng = _pickup_point(order)
    candidates: list[Candidate] = []
    for profile, _user in rows:
        if not profile.has_capacity:
            continue
        if exclude_agent_id and profile.user_id == exclude_agent_id:
            continue
        a_lat, a_lng = _agent_point(profile)
        distance = None
        if None not in (p_lat, p_lng, a_lat, a_lng):
            distance = round(haversine_km(a_lat, a_lng, p_lat, p_lng), 3)
        candidates.append(
            Candidate(
                profile=profile,
                distance_km=distance,
                zone_match=bool(order.pickup_zone_id and profile.home_zone_id == order.pickup_zone_id),
                load_ratio=profile.active_orders / max(profile.max_active_orders, 1),
            )
        )
    return sorted(candidates, key=lambda c: c.score)


def find_nearest_agent(
    db: Session, order: Order, exclude_agent_id: int | None = None
) -> Candidate | None:
    ranked = rank_candidates(db, order, exclude_agent_id=exclude_agent_id)
    if not ranked and exclude_agent_id:
        # nobody else is free — fall back to the excluded agent rather than
        # leaving the parcel stranded
        ranked = rank_candidates(db, order)
    return ranked[0] if ranked else None


def attach_agent(db: Session, order: Order, agent_user_id: int, release_previous: bool = True) -> None:
    """Move load counters when an order changes hands.

    `release_previous=False` is used on the reschedule path, where the previous
    agent's capacity was already given back when the attempt failed.
    """
    if order.agent_id == agent_user_id:
        return
    if order.agent_id and release_previous:
        release_agent(db, order.agent_id)
    profile = db.scalar(select(AgentProfile).where(AgentProfile.user_id == agent_user_id))
    if profile:
        profile.active_orders += 1
    order.agent_id = agent_user_id


def release_agent(db: Session, agent_user_id: int | None) -> None:
    if not agent_user_id:
        return
    profile = db.scalar(select(AgentProfile).where(AgentProfile.user_id == agent_user_id))
    if profile and profile.active_orders > 0:
        profile.active_orders -= 1
