"""Sync jobs with JobRun audit logging.

Long-running operations are wrapped here as named, self-contained functions
that open their own DB session. Phase 1 calls them synchronously from routes
(off-thread) and from APScheduler; later phases can hand the exact same
functions to background workers without touching call sites (rule #14).
"""

import logging
from datetime import datetime, timezone

from app.db.models import Channel, JobRun
from app.db.session import SessionLocal
from app.services import channel_service

logger = logging.getLogger(__name__)


def _finish(run: JobRun, status: str, detail: dict | None) -> None:
    run.status = status
    run.finished_at = datetime.now(timezone.utc)
    run.detail = detail


def sync_all_channels_job() -> dict:
    """Refresh every tracked channel. Safe to call from scheduler or route."""
    db = SessionLocal()
    try:
        run = JobRun(job_name="sync_all_channels")
        db.add(run)
        db.commit()

        results = []
        try:
            for channel in channel_service.list_channels(db):
                try:
                    results.append(channel_service.sync_channel(db, channel))
                except Exception as exc:  # noqa: BLE001 - keep other channels going
                    logger.warning("Sync failed for %s: %s", channel.youtube_channel_id, exc)
                    results.append(
                        {"channel": channel.youtube_channel_id, "error": str(exc)}
                    )
            _finish(run, "success", {"channels": results})
        except Exception as exc:  # noqa: BLE001
            _finish(run, "failed", {"error": str(exc)})
        db.commit()
        return {"job_run_id": run.id, "results": results}
    finally:
        db.close()


def sync_single_channel_job(channel_pk: int) -> dict:
    db = SessionLocal()
    try:
        run = JobRun(job_name=f"sync_channel:{channel_pk}")
        db.add(run)
        db.commit()
        channel = channel_service.get_channel_by_pk(db, channel_pk)
        if channel is None:
            _finish(run, "failed", {"error": f"channel {channel_pk} not found"})
            db.commit()
            raise ValueError(f"Channel {channel_pk} not found")
        try:
            detail = channel_service.sync_channel(db, channel)
            _finish(run, "success", detail)
            db.commit()
            return {"job_run_id": run.id, **detail}
        except Exception as exc:  # noqa: BLE001
            _finish(run, "failed", {"error": str(exc)})
            db.commit()
            raise
        finally:
            db.close()
    except ValueError:
        raise
