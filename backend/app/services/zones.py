"""Zone detection.

Resolution order for a pincode:
  1. exact serviceable Area row  -> its zone
  2. longest matching pincode prefix among serviceable areas (handles new
     pincodes in an already-mapped sector, e.g. 600045 falling back to 6000xx)
  3. unserviceable -> ZoneNotFound, surfaced to the caller as HTTP 422

No zone boundaries are hardcoded; everything comes from the areas table.
"""
from __future__ import annotations

from math import asin, cos, radians, sin, sqrt

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Area, Zone


class ZoneNotFound(Exception):
    def __init__(self, pincode: str):
        self.pincode = pincode
        super().__init__(f"Pincode {pincode} is not mapped to a serviceable zone.")


def detect_zone(db: Session, pincode: str) -> tuple[Zone, Area | None]:
    pincode = (pincode or "").strip()
    if not pincode:
        raise ZoneNotFound(pincode)

    area = db.scalar(
        select(Area).where(Area.pincode == pincode, Area.is_serviceable.is_(True))
    )
    if area and area.zone and area.zone.is_active:
        return area.zone, area

    # prefix fallback, longest prefix wins
    candidates = db.scalars(select(Area).where(Area.is_serviceable.is_(True))).all()
    best: Area | None = None
    for cand in candidates:
        if not cand.zone or not cand.zone.is_active:
            continue
        common = _common_prefix_len(cand.pincode, pincode)
        if common >= 3 and (best is None or common > _common_prefix_len(best.pincode, pincode)):
            best = cand
    if best:
        return best.zone, None

    raise ZoneNotFound(pincode)


def _common_prefix_len(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in km."""
    r = 6371.0
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    return 2 * r * asin(sqrt(a))
