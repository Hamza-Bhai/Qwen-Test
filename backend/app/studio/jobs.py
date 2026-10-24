"""Growth Studio background jobs (same shape as ``app.jobs.sync_jobs``).

Each job opens its own session and writes a ``JobRun`` row, so the same
functions can be called by the in-process scheduler or by a manual button.
"""

import logging
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import JobRun
from app.db.session import SessionLocal
from app.services.channel_service import get_service
from app.studio.evidence import build_evidence
from app.studio.models import StudioAdvisorReport, StudioGoogleConnection
from app.studio.services.advisor import generate_advice
from app.studio.services.ai_client import AIClient
from app.studio.services.competitor_sync import sync_all_watching
from app.studio.services.discovery import discover
from app.studio.services.ideas import generate_ideas
from app.studio.services.my_channel import pull_all_connected
from app.studio.services.planner import update_outcomes
from app.studio.services.serpapi import SerpApiClient
from app.studio.services.settings_store import get_auto_discovery

logger = logging.getLogger(__name__)

SYNC_JOB = "studio_competitor_sync"
DISCOVERY_JOB = "studio_competitor_discovery"
MY_CHANNELS_JOB = "studio_my_channels_pull"
ADVISOR_JOB = "studio_advisor_daily"


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


def my_channels_pull_job() -> dict:
    """Pull private analytics for every connected own channel (daily)."""
    db = SessionLocal()
    try:
        run = JobRun(job_name=MY_CHANNELS_JOB)
        db.add(run)
        db.commit()
        results: list[dict] = []
        try:
            results = pull_all_connected(db)
            _finish(run, "success", {"results": results})
        except Exception as exc:  # noqa: BLE001
            logger.warning("My-channels pull job failed: %s", exc)
            _finish(run, "failed", {"error": str(exc)})
        db.commit()
        return {"job_run_id": run.id, "results": results}
    finally:
        db.close()


def _ideas_done_today(db: Session, connection_id: int, today) -> bool:
    for report in db.scalars(select(StudioAdvisorReport).where(
        StudioAdvisorReport.connection_id == connection_id, StudioAdvisorReport.kind == "ideas",
    ).order_by(StudioAdvisorReport.id.desc()).limit(5)):
        created = report.created_at if report.created_at.tzinfo else report.created_at.replace(tzinfo=timezone.utc)
        if created.date() == today:
            return True
    return False


def _advise_channel(db: Session, connection: StudioGoogleConnection, today) -> dict:
    result: dict = {"connection_id": connection.id, "outcomes": update_outcomes(db, connection, today=today)}
    ai = AIClient(db)
    if not get_settings().ai_api_key:
        result["ai"] = "not configured"
        return result
    if _ideas_done_today(db, connection.id, today):
        result["ai"] = "already done today"
        return result
    serpapi = SerpApiClient(db) if get_settings().serpapi_key else None
    pack = build_evidence(db, connection, serpapi=serpapi, today=today)
    for name, step in (("advice", generate_advice), ("ideas", generate_ideas)):
        try:
            step(db, connection, ai, pack, today=today)
            result[name] = "ok"
        except Exception as exc:  # noqa: BLE001 - the other step and channels still run
            db.rollback()
            result[f"{name}_error"] = str(exc)
    return result


def advisor_daily_job() -> dict:
    """Outcomes for published ideas, then today's advice and ideas for every connected channel."""
    db = SessionLocal()
    try:
        run = JobRun(job_name=ADVISOR_JOB)
        db.add(run)
        db.commit()
        results: list[dict] = []
        today = datetime.now(timezone.utc).date()
        try:
            connections = db.scalars(select(StudioGoogleConnection).where(
                StudioGoogleConnection.status == "connected").order_by(StudioGoogleConnection.id)).all()
            for connection in connections:
                try:
                    results.append(_advise_channel(db, connection, today))
                except Exception as exc:  # noqa: BLE001
                    db.rollback()
                    results.append({"connection_id": connection.id, "error": str(exc)})
            _finish(run, "success", {"results": results})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Advisor job failed: %s", exc)
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
