"""In-process APScheduler wiring (Phase 1).

Deliberately minimal: no Celery/Redis (rule #13). When scale demands, replace
this with an external worker that triggers the same job functions.
"""

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import get_settings
from app.jobs.sync_jobs import sync_all_channels_job
from app.studio.jobs import (
    DISCOVERY_JOB,
    MY_CHANNELS_JOB,
    SYNC_JOB,
    competitor_discovery_job,
    competitor_sync_job,
    job_overdue,
    my_channels_pull_job,
)
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

    def catch_up(job_name: str, max_age: timedelta) -> dict:
        """Run a missed job shortly after start (the app is only up while the PC is on)."""
        db = SessionLocal()
        try:
            overdue = job_overdue(db, job_name, max_age)
        finally:
            db.close()
        return {"next_run_time": datetime.now(timezone.utc) + timedelta(seconds=60)} if overdue else {}

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        sync_all_channels_job,
        IntervalTrigger(hours=6),
        id="sync_all_channels",
        replace_existing=True,
    )
    _scheduler.add_job(
        competitor_sync_job,
        IntervalTrigger(hours=24),
        id=SYNC_JOB,
        replace_existing=True,
        **catch_up(SYNC_JOB, timedelta(hours=24)),
    )
    _scheduler.add_job(
        my_channels_pull_job,
        IntervalTrigger(hours=24),
        id=MY_CHANNELS_JOB,
        replace_existing=True,
        **catch_up(MY_CHANNELS_JOB, timedelta(hours=24)),
    )
    # Skips itself (recorded as "skipped") unless the owner switched auto-discovery on.
    _scheduler.add_job(
        competitor_discovery_job,
        IntervalTrigger(weeks=1),
        id=DISCOVERY_JOB,
        replace_existing=True,
        **catch_up(DISCOVERY_JOB, timedelta(days=7)),
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
