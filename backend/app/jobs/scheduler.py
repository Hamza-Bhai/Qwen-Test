"""In-process APScheduler wiring (Phase 1).

Deliberately minimal: no Celery/Redis (rule #13). When scale demands, replace
this with an external worker that triggers the same job functions.
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import get_settings
from app.jobs.sync_jobs import sync_all_channels_job
from app.services.cache import ResponseCache
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler
    settings = get_settings()
    if not settings.enable_scheduler:
        return None
    if _scheduler is not None:
        return _scheduler

    def cache_maintenance() -> None:
        db = SessionLocal()
        try:
            removed = ResponseCache(db).purge_expired()
            if removed:
                logger.info("Purged %d expired cache rows", removed)
        finally:
            db.close()

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        sync_all_channels_job,
        IntervalTrigger(hours=6),
        id="sync_all_channels",
        replace_existing=True,
    )
    _scheduler.add_job(
        cache_maintenance,
        IntervalTrigger(minutes=30),
        id="cache_maintenance",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("APScheduler started (in-process)")
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def scheduler_running() -> bool:
    return _scheduler is not None and _scheduler.running
