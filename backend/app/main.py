from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .database import Base, engine
from .models import ImmutableRecordError
from .routers import admin, agent, auth, orders, public
from .services.rate_engine import RateConfigError
from .services.zones import ZoneNotFound

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("lastmile")

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    if settings.AUTO_SEED_ON_BOOT:
        from .seed import run as run_seed

        try:
            run_seed()
        except Exception as exc:  # noqa: BLE001 - never block boot on seed
            log.warning("Seed skipped: %s", exc)
    log.info("%s ready (%s)", settings.APP_NAME, settings.ENV)
    yield


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description=(
        "Delivery management platform: configurable rate engine (zone detection, "
        "volumetric weight, B2B/B2C rate cards, COD surcharge), agent auto-assignment, "
        "immutable order tracking and status notifications."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.CORS_ORIGINS.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ZoneNotFound)
async def zone_not_found_handler(request: Request, exc: ZoneNotFound):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(RateConfigError)
async def rate_config_handler(request: Request, exc: RateConfigError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(ImmutableRecordError)
async def immutable_handler(request: Request, exc: ImmutableRecordError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


app.include_router(auth.router)
app.include_router(public.router)
app.include_router(orders.router)
app.include_router(agent.router)
app.include_router(admin.router)


# --------------------------------------------------------------------------- #
# Frontend (single service deploy: API + UI from one process)
# --------------------------------------------------------------------------- #
if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR), name="assets")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(FRONTEND_DIR / "index.html")
