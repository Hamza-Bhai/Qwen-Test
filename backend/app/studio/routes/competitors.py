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
from app.studio.routes.errors import raise_http
from app.studio.services import competitors as svc

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
