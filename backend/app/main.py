"""FastAPI application entrypoint (Phase 1)."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.routes import channels, jobs, system
from app.core.config import get_settings
from app.db.session import init_db
from app.jobs.scheduler import start_scheduler, stop_scheduler
from app.studio.routes import advisor as studio_advisor
from app.studio.routes import competitors as studio_competitors
from app.studio.routes import my_channel as studio_my_channel
from app.studio.routes import settings as studio_settings

logging.basicConfig(level=logging.INFO)
# httpx/httpcore log every request URL at INFO; keep them quiet so outgoing
# request details never land in the server logs.
for _noisy in ("httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    logger.info("Backend startup complete")
    yield
    stop_scheduler()


settings = get_settings()
app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)


# Registered before CORSMiddleware so it runs *inside* it: an unexpected error then
# still carries CORS headers, and the browser shows a real 500 instead of
# "cannot reach the backend". Details stay in the server log.
@app.middleware("http")
async def unexpected_errors_as_json(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception:  # noqa: BLE001
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "The backend hit an unexpected error. Details are in the backend terminal."},
        )


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_PREFIX = "/api/v1"
app.include_router(system.router, prefix=API_PREFIX)
app.include_router(channels.router, prefix=API_PREFIX)
app.include_router(jobs.router, prefix=API_PREFIX)
app.include_router(studio_settings.router, prefix=API_PREFIX)
app.include_router(studio_competitors.router, prefix=API_PREFIX)
app.include_router(studio_my_channel.router, prefix=API_PREFIX)
app.include_router(studio_advisor.router, prefix=API_PREFIX)


@app.get("/")
def root() -> dict:
    return {"name": settings.app_name, "version": __version__, "docs": "/docs"}
