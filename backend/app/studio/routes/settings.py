"""Growth Studio status and settings endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.studio.schemas import (
    KeywordsPayload,
    OwnChannelBrief,
    OwnChannelDetail,
    OwnChannelPayload,
    OwnChannelResponse,
    SerpApiStatus,
    StudioStatus,
)
from app.studio.services.own_channel import (
    get_own_channel,
    list_own_channel_candidates,
    set_own_channel,
)
from app.studio.services.serpapi import SerpApiClient
from app.studio.services.settings_store import get_keywords, set_keywords

router = APIRouter(prefix="/studio", tags=["studio"])


@router.get("/status", response_model=StudioStatus)
def studio_status(db: Session = Depends(get_db)) -> StudioStatus:
    usage = SerpApiClient(db).usage()
    return StudioStatus(
        serpapi=SerpApiStatus(
            configured=bool(get_settings().serpapi_key),
            month=usage.month,
            used=usage.used,
            cap=usage.cap,
            remaining=usage.remaining,
        ),
        keywords=get_keywords(db),
        own_channel=_brief(get_own_channel(db)),
    )


def _brief(channel) -> OwnChannelBrief | None:
    return OwnChannelBrief(id=channel.id, title=channel.title) if channel else None


def _own_channel_response(db: Session) -> OwnChannelResponse:
    channel = get_own_channel(db)
    return OwnChannelResponse(
        channel=OwnChannelDetail(
            id=channel.id, title=channel.title, subscriber_count=channel.subscriber_count
        )
        if channel
        else None,
        candidates=[OwnChannelBrief(id=c.id, title=c.title) for c in list_own_channel_candidates(db)],
    )


@router.get("/own-channel", response_model=OwnChannelResponse)
def read_own_channel(db: Session = Depends(get_db)) -> OwnChannelResponse:
    return _own_channel_response(db)


@router.put("/own-channel", response_model=OwnChannelResponse)
def write_own_channel(payload: OwnChannelPayload, db: Session = Depends(get_db)) -> OwnChannelResponse:
    try:
        set_own_channel(db, payload.channel_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return _own_channel_response(db)


@router.get("/keywords", response_model=KeywordsPayload)
def read_keywords(db: Session = Depends(get_db)) -> KeywordsPayload:
    return KeywordsPayload(keywords=get_keywords(db))


@router.put("/keywords", response_model=KeywordsPayload)
def write_keywords(payload: KeywordsPayload, db: Session = Depends(get_db)) -> KeywordsPayload:
    try:
        return KeywordsPayload(keywords=set_keywords(db, payload.keywords))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
