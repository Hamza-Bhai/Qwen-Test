"""System-level endpoints: health and quota/cache observability."""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import __version__
from app.core.config import get_settings
from app.db.models import ApiCache, QuotaUsage
from app.db.session import get_db
from app.jobs.scheduler import scheduler_running
from app.schemas.common import HealthResponse
from app.schemas.quota import CacheStats, QuotaStatus
from app.services.quota import QuotaTracker

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        version=__version__,
        scheduler_running=scheduler_running(),
    )


@router.get("/quota", response_model=QuotaStatus)
def quota_status(db: Session = Depends(get_db)) -> QuotaStatus:
    settings = get_settings()
    tracker = QuotaTracker(db)
    used = tracker.used_today()
    return QuotaStatus(
        service="youtube",
        day=tracker.today,
        units_used=used,
        daily_limit=settings.youtube_quota_daily_limit,
        remaining=max(settings.youtube_quota_daily_limit - used, 0),
    )


@router.get("/cache/stats", response_model=CacheStats)
def cache_stats(db: Session = Depends(get_db)) -> CacheStats:
    from datetime import datetime, timezone

    entries = db.execute(select(func.count(ApiCache.id))).scalar_one()
    valid = db.execute(
        select(func.count(ApiCache.id)).where(ApiCache.expires_at > datetime.now(timezone.utc))
    ).scalar_one()
    return CacheStats(entries=entries, valid_entries=valid)
