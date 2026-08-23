from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Area, Zone
from ..schemas import AreaOut, ZoneOut
from ..services.zones import ZoneNotFound, detect_zone

router = APIRouter(prefix="/api", tags=["public"])


@router.get("/health")
def health():
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "env": settings.ENV,
        "email_provider": "smtp" if settings.email_configured else "simulated",
        "sms_provider": settings.SMS_PROVIDER if settings.sms_configured else "simulated",
    }


@router.get("/zones", response_model=list[ZoneOut])
def public_zones(db: Session = Depends(get_db)):
    return db.scalars(select(Zone).where(Zone.is_active.is_(True)).order_by(Zone.code)).all()


@router.get("/serviceable", response_model=list[AreaOut])
def serviceable_areas(db: Session = Depends(get_db)):
    return db.scalars(
        select(Area).where(Area.is_serviceable.is_(True)).order_by(Area.pincode)
    ).all()


@router.get("/zone-lookup/{pincode}", response_model=ZoneOut)
def zone_lookup(pincode: str, db: Session = Depends(get_db)):
    """Used by the booking form to show the detected zone as the user types."""
    try:
        zone, _ = detect_zone(db, pincode)
    except ZoneNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return zone
