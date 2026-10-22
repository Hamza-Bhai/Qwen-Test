"""Endpoints that trigger named jobs (also callable by future workers)."""

import asyncio

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.db.models import JobRun
from app.db.session import get_db
from fastapi import Depends
from sqlalchemy.orm import Session
from app.jobs.sync_jobs import sync_all_channels_job

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/sync-all")
async def trigger_sync_all():
    result = await asyncio.to_thread(sync_all_channels_job)
    return result


@router.get("/runs")
def list_runs(limit: int = 20, db: Session = Depends(get_db)):
    rows = db.execute(
        select(JobRun).order_by(JobRun.started_at.desc()).limit(min(limit, 100))
    ).scalars().all()
    return [
        {
            "id": r.id,
            "job_name": r.job_name,
            "status": r.status,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
            "detail": r.detail,
        }
        for r in rows
    ]
