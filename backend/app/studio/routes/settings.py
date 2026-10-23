"""Growth Studio status and settings endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.studio.schemas import KeywordsPayload, SerpApiStatus, StudioStatus
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
    )


@router.get("/keywords", response_model=KeywordsPayload)
def read_keywords(db: Session = Depends(get_db)) -> KeywordsPayload:
    return KeywordsPayload(keywords=get_keywords(db))


@router.put("/keywords", response_model=KeywordsPayload)
def write_keywords(payload: KeywordsPayload, db: Session = Depends(get_db)) -> KeywordsPayload:
    try:
        return KeywordsPayload(keywords=set_keywords(db, payload.keywords))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
