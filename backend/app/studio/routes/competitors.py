"""Growth Studio competitor endpoints."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.quota import QuotaExceededError
from app.services.youtube import YouTubeAPIError
from app.studio.models import StudioCompetitor, StudioCompetitorVideo
from app.studio.routes.deps import get_serpapi
from app.studio.routes.errors import raise_http
from app.studio.services import competitor_sync, hooks, insights
from app.studio.services import competitors as svc
from app.studio.services.serpapi import SerpApiClient, SerpApiError

router = APIRouter(prefix="/studio/competitors", tags=["studio"])


class CompetitorRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    youtube_channel_id: str
    title: str
    handle: str | None
    thumbnail_url: str | None
    country: str | None
    subscriber_count: int | None
    video_count: int | None
    status: str
    source: str
    discovery_reason: dict | None
    notes: str | None
    added_at: datetime
    last_synced_at: datetime | None
    videos_stored: int = 0


class CompetitorCreate(BaseModel):
    identifier: str
    notes: str | None = None


class CompetitorPatch(BaseModel):
    status: str | None = None
    notes: str | None = None


def _read(db: Session, comps: list[StudioCompetitor]) -> list[CompetitorRead]:
    counts = dict(
        db.execute(
            select(StudioCompetitorVideo.competitor_id, func.count()).group_by(StudioCompetitorVideo.competitor_id)
        ).all()
    )
    out = []
    for comp in comps:
        item = CompetitorRead.model_validate(comp)
        item.videos_stored = int(counts.get(comp.id, 0))
        out.append(item)
    return out


def _get_or_404(db: Session, competitor_id: int) -> StudioCompetitor:
    comp = svc.get_competitor(db, competitor_id)
    if comp is None:
        raise HTTPException(status_code=404, detail="Competitor not found")
    return comp


@router.get("", response_model=list[CompetitorRead])
def list_competitors(status: str | None = None, db: Session = Depends(get_db)):
    return _read(db, svc.list_competitors(db, status))


@router.post("", response_model=CompetitorRead, status_code=201)
def add_competitor(payload: CompetitorCreate, db: Session = Depends(get_db)):
    try:
        comp = svc.add_competitor(db, payload.identifier, notes=payload.notes)
    except (YouTubeAPIError, QuotaExceededError, ValueError) as exc:
        raise_http(exc)
    return _read(db, [comp])[0]


@router.get("/insights")
def competitor_insights(db: Session = Depends(get_db)):
    return insights.build_insights(db)


@router.get("/new-uploads")
def competitor_new_uploads(db: Session = Depends(get_db)):
    return insights.new_uploads(db)


@router.post("/mark-seen")
def competitor_mark_seen(db: Session = Depends(get_db)):
    return {"marked": insights.mark_seen(db)}


@router.patch("/{competitor_id}", response_model=CompetitorRead)
def patch_competitor(competitor_id: int, payload: CompetitorPatch, db: Session = Depends(get_db)):
    _get_or_404(db, competitor_id)
    try:
        if payload.status is not None:
            svc.set_status(db, competitor_id, payload.status)
        if "notes" in payload.model_fields_set:
            svc.update_notes(db, competitor_id, payload.notes)
    except ValueError as exc:
        raise_http(exc)
    return _read(db, [_get_or_404(db, competitor_id)])[0]


@router.delete("/{competitor_id}", status_code=204)
def delete_competitor(competitor_id: int, db: Session = Depends(get_db)):
    _get_or_404(db, competitor_id)
    svc.remove_competitor(db, competitor_id)


@router.post("/sync")
def sync_all(db: Session = Depends(get_db)):
    return {"results": competitor_sync.sync_all_watching(db)}


@router.post("/{competitor_id}/sync")
def sync_one(competitor_id: int, db: Session = Depends(get_db)):
    comp = _get_or_404(db, competitor_id)
    if comp.status != "watching":
        raise HTTPException(status_code=409, detail="Only competitors on the watchlist can be synced.")
    try:
        return competitor_sync.sync_competitor(db, comp)
    except (YouTubeAPIError, QuotaExceededError, ValueError) as exc:
        raise_http(exc)


@router.post("/videos/{youtube_video_id}/hook")
def study_video_hook(youtube_video_id: str, db: Session = Depends(get_db), serp: SerpApiClient = Depends(get_serpapi)):
    try:
        return hooks.study_hook(db, serp, youtube_video_id)
    except (hooks.UnknownVideo, hooks.NoTranscript) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except (SerpApiError, ValueError) as exc:
        raise_http(exc)


@router.get("/videos/{youtube_video_id}/hook")
def read_video_hook(youtube_video_id: str, db: Session = Depends(get_db)):
    stored = hooks.stored_hook(db, youtube_video_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="No hook stored for this video yet")
    return stored

