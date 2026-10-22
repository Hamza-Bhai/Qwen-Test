"""Channel CRUD + sync endpoints.

Handlers are ``def`` (not ``async def``): FastAPI runs them in a threadpool so
the event loop stays free while YouTube calls happen — simple Phase 1 approach
that keeps route signatures identical when jobs move to workers later (rule #14).
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.analytics.metrics import summarize_channel
from app.db.session import get_db
from app.schemas.channel import ChannelCreate, ChannelRead, SyncResult, VideoRead
from app.services import channel_service
from app.services.quota import QuotaExceededError
from app.services.youtube import YouTubeAPIError

router = APIRouter(prefix="/channels", tags=["channels"])


def _handle_api_error(exc: Exception) -> None:
    if isinstance(exc, YouTubeAPIError):
        status = 502 if exc.status_code not in (403, 404) else (404 if exc.status_code == 404 else 400)
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    if isinstance(exc, QuotaExceededError):
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("", response_model=list[ChannelRead])
def list_channels(db: Session = Depends(get_db)):
    return channel_service.list_channels(db)


@router.post("", response_model=ChannelRead, status_code=201)
async def add_channel(payload: ChannelCreate, db: Session = Depends(get_db)):
    """Resolve a handle/ID via the YouTube Data API and store real metadata."""
    try:
        channel = await asyncio.to_thread(channel_service.add_channel, db, payload.identifier)
    except (YouTubeAPIError, QuotaExceededError, ValueError) as exc:
        _handle_api_error(exc)
    return channel


@router.get("/{channel_id}", response_model=ChannelRead)
def get_channel(channel_id: int, db: Session = Depends(get_db)):
    channel = channel_service.get_channel_by_pk(db, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    return channel


@router.delete("/{channel_id}", status_code=204)
def delete_channel(channel_id: int, db: Session = Depends(get_db)):
    channel = channel_service.get_channel_by_pk(db, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    channel_service.delete_channel(db, channel)


@router.get("/{channel_id}/videos", response_model=list[VideoRead])
def list_videos(
    channel_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    channel = channel_service.get_channel_by_pk(db, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    return channel_service.list_videos(db, channel_id, limit=limit)


@router.get("/{channel_id}/summary")
def channel_summary(channel_id: int, db: Session = Depends(get_db)):
    """Deterministic analytics only — no AI involvement here (rule #9)."""
    channel = channel_service.get_channel_by_pk(db, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    summary = summarize_channel(db, channel)
    return summary.__dict__


@router.post("/{channel_id}/sync", response_model=SyncResult)
async def sync_channel(channel_id: int, db: Session = Depends(get_db)):
    try:
        existing = channel_service.get_channel_by_pk(db, channel_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Channel not found")
        detail = await asyncio.to_thread(channel_service.sync_channel, db, existing)
    except HTTPException:
        raise
    except (YouTubeAPIError, QuotaExceededError, ValueError) as exc:
        _handle_api_error(exc)
    return SyncResult(
        channel_id=channel_id,
        videos_upserted=detail["videos_upserted"],
        quota_units_used=detail["quota_units_used"],
    )
