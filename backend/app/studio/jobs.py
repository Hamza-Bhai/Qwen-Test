"""Growth Studio background jobs (same shape as ``app.jobs.sync_jobs``).

Each job opens its own session and writes a ``JobRun`` row, so the same
functions can be called by the in-process scheduler or by a manual button.
"""

import logging
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import JobRun
from app.db.session import SessionLocal
from app.services.channel_service import get_service
from app.studio.services.competitor_sync import sync_all_watching
from app.studio.services.discovery import discover
from app.studio.services.serpapi import SerpApiClient
from app.studio.services.settings_store import get_auto_discovery

logger = logging.getLogger(__name__)

SYNC_JOB = "studio_competitor_sync"
DISCOVERY_JOB = "studio_competitor_discovery"


def _finish(run: JobRun, status: str, detail: dict | None) -> None:
    run.status = status
    run.finished_at = datetime.now(timezone.utc)
    run.detail = detail


def competitor_sync_job() -> dict:
    """Refresh every watched competitor (daily)."""
    db = SessionLocal()
    try:
        run = JobRun(job_name=SYNC_JOB)
        db.add(run)
        db.commit()
        results: list[dict] = []
        try:
            results = sync_all_watching(db)
            _finish(run, "success", {"results": results})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Competitor sync job failed: %s", exc)
            _finish(run, "failed", {"error": str(exc)})
        db.commit()
        return {"job_run_id": run.id, "results": results}
    finally:
        db.close()


def competitor_discovery_job() -> dict:
    """Weekly discovery. Spends SerpApi searches, so it only runs when the owner
    switched auto-discovery on; otherwise it records a ``skipped`` run."""
    db = SessionLocal()
    try:
        run = JobRun(job_name=DISCOVERY_JOB)
        db.add(run)
        db.commit()
        if not get_auto_discovery(db):
            detail = {"skipped": "auto_discovery is off"}
            _finish(run, "skipped", detail)
            db.commit()
            return {"job_run_id": run.id, **detail}
        try:
            result = discover(db, SerpApiClient(db), get_service(db))
            detail = asdict(result)
            _finish(run, "success", detail)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Competitor discovery job failed: %s", exc)
            detail = {"error": str(exc)}
            _finish(run, "failed", detail)
        db.commit()
        return {"job_run_id": run.id, **detail}
    finally:
        db.close()


def job_overdue(db: Session, job_name: str, max_age: timedelta, now: datetime | None = None) -> bool:
    """True when there is no successful run of ``job_name`` newer than ``max_age``."""
    now = now or datetime.now(timezone.utc)
    last = db.scalars(
        select(JobRun)
        .where(JobRun.job_name == job_name, JobRun.status == "success")
        .order_by(JobRun.started_at.desc())
    ).first()
    if last is None:
        return True
    started = last.started_at if last.started_at.tzinfo else last.started_at.replace(tzinfo=timezone.utc)
    return now - started > max_age
