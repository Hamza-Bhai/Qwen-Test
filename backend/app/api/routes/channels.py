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
from app.schemas.channel import (
    ChannelAnalysisRead,
    ChannelAuditRead,
    ChannelCreate,
    ChannelOverview,
    ChannelRead,
    SyncResult,
    VideoRead,
)
from app.services import audit_service, channel_service
from app.services.quota import QuotaExceededError
from app.services.url_resolver import UnsupportedURLError
from app.services.youtube import YouTubeAPIError

router = APIRouter(prefix="/channels", tags=["channels"])


def _handle_api_error(exc: Exception) -> None:
    if isinstance(exc, UnsupportedURLError):
        # 400 + explicit "unsupported format" signal; never a silent guess.
        raise HTTPException(
            status_code=400,
            detail={
                "code": "unsupported_url_format",
                "url": exc.url,
                "format": exc.kind,
                "message": str(exc),
            },
        ) from exc
    if isinstance(exc, YouTubeAPIError):
        if exc.status_code == 404:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if exc.status_code == 403 or exc.status_code >= 500:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, QuotaExceededError):
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("", response_model=list[ChannelRead])
def list_channels(db: Session = Depends(get_db)):
    return channel_service.list_channels(db)


@router.post("", response_model=ChannelRead, status_code=201)
async def add_channel(payload: ChannelCreate, db: Session = Depends(get_db)):
    """Resolve a URL/handle/ID via the YouTube Data API and store real metadata."""
    try:
        channel = await asyncio.to_thread(channel_service.add_channel, db, payload.identifier)
    except (YouTubeAPIError, QuotaExceededError, ValueError) as exc:
        _handle_api_error(exc)
    return channel


@router.post("/analyze", response_model=ChannelOverview, status_code=201)
async def analyze_channel(payload: ChannelCreate, db: Session = Depends(get_db)):
    """One-shot Phase-1 action behind the 'Analyze Channel' button.

    Accepts any supported YouTube channel URL, resolves it against the real
    Data API, syncs recent videos, records an analysis-history row and returns
    the full overview in a single response.
    """
    from dataclasses import asdict

    try:
        channel, detail = await asyncio.to_thread(
            channel_service.analyze_channel, db, payload.identifier
        )
    except (YouTubeAPIError, QuotaExceededError, ValueError) as exc:
        _handle_api_error(exc)
    summary = summarize_channel(db, channel)
    return ChannelOverview(
        channel=ChannelRead.model_validate(channel),
        summary=asdict(summary),
        recent_videos=[
            VideoRead.model_validate(v)
            for v in channel_service.list_videos(db, channel.id, limit=24)
        ],
        history=[
            ChannelAnalysisRead.model_validate(a)
            for a in channel_service.list_analyses(db, channel.id)
        ],
    )


@router.get("/{channel_id}/overview", response_model=ChannelOverview)
def channel_overview(channel_id: int, db: Session = Depends(get_db)):
    """Dashboard view: stored channel data + deterministic summary + history.

    Reads from our database only (no quota spent); refresh with /sync first.
    """
    from dataclasses import asdict

    channel = channel_service.get_channel_by_pk(db, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    summary = summarize_channel(db, channel)
    return ChannelOverview(
        channel=ChannelRead.model_validate(channel),
        summary=asdict(summary),
        recent_videos=[
            VideoRead.model_validate(v)
            for v in channel_service.list_videos(db, channel_id, limit=24)
        ],
        history=[
            ChannelAnalysisRead.model_validate(a)
            for a in channel_service.list_analyses(db, channel_id)
        ],
    )


@router.get("/{channel_id}/audit", response_model=ChannelAuditRead)
def channel_audit(channel_id: int, db: Session = Depends(get_db)):
    """Phase-2 rule-based channel audit. Read-only: never writes history.

    Pure DB reads — zero YouTube quota. Every finding ships with evidence,
    confidence and data limitations; insufficient data => 'inconclusive'.
    """
    channel = channel_service.get_channel_by_pk(db, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    return audit_service.audit_channel(db, channel)


@router.post("/{channel_id}/audit", response_model=ChannelAuditRead)
def rerun_channel_audit(channel_id: int, db: Session = Depends(get_db)):
    """Explicit 'Re-run audit' action: same audit, plus one 'audit' history row."""
    channel = channel_service.get_channel_by_pk(db, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    return audit_service.audit_channel(db, channel, persist=True)


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
        detail = await asyncio.to_thread(channel_service.sync_channel_by_user, db, existing)
    except HTTPException:
        raise
    except (YouTubeAPIError, QuotaExceededError, ValueError) as exc:
        _handle_api_error(exc)
    return SyncResult(
        channel_id=channel_id,
        videos_upserted=detail["videos_upserted"],
        quota_units_used=detail["quota_units_used"],
    )
