"""Growth Studio advisor (Dashboard) and ideas/planner (Ideas & Plan) endpoints, per connected channel."""

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.studio.evidence import build_evidence
from app.studio.models import StudioGoogleConnection, StudioIdea, StudioIdeaOutcome
from app.studio.services import planner
from app.studio.services.advisor import (
    QUESTION_LABELS,
    AdviceInProgress,
    AdviceRejected,
    generate_advice,
    latest_advice,
)
from app.studio.services.ai_client import AIBudgetExceeded, AIClient, AIError, AINotConfigured
from app.studio.services.ideas import IdeasInProgress, generate_ideas
from app.studio.services.my_channel import get_overview
from app.studio.services.serpapi import SerpApiClient

router = APIRouter(prefix="/studio", tags=["studio"])


def serpapi_or_none(db: Session) -> SerpApiClient | None:
    return SerpApiClient(db) if get_settings().serpapi_key else None


def _connection(db: Session, connection_id: int) -> StudioGoogleConnection:
    connection = db.get(StudioGoogleConnection, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="That channel is not connected.")
    return connection


def _run_ai(fn):
    """Call an AI step and map its errors to HTTP codes."""
    try:
        return fn()
    except (AINotConfigured, AdviceInProgress, IdeasInProgress) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except AIBudgetExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from None
    except AdviceRejected as exc:  # IdeasRejected too
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except AIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None


def _pack(db: Session, connection: StudioGoogleConnection) -> dict:
    return build_evidence(db, connection, serpapi=serpapi_or_none(db), today=datetime.now(timezone.utc).date())


@router.get("/advisor/{connection_id}")
def read_advisor(connection_id: int, db: Session = Depends(get_db)) -> dict:
    connection = _connection(db, connection_id)
    ai = AIClient(db)
    report = latest_advice(db, connection)
    return {
        "ai": {"configured": ai.configured, "used_today": ai.used_today(), "cap": ai.cap},
        "questions": QUESTION_LABELS,
        "advice": {**report.payload, "created_at": report.created_at.isoformat(), "model": report.model} if report else None,
        "findings": get_overview(db, connection)["findings"],
    }


@router.post("/advisor/{connection_id}/generate")
def create_advice(connection_id: int, force: bool = False, db: Session = Depends(get_db)) -> dict:
    connection = _connection(db, connection_id)
    ai = AIClient(db)

    def run():
        if not ai.configured:  # before building the pack, so no SerpApi search is spent
            raise AINotConfigured("AI is not configured: add AI_API_KEY (and AI_BASE_URL / AI_MODEL) to backend/.env.")
        return generate_advice(db, connection, ai, _pack(db, connection), force=force)

    report = _run_ai(run)
    return {**report.payload, "created_at": report.created_at.isoformat(), "model": report.model}


def _idea_out(db: Session, idea: StudioIdea) -> dict:
    outcomes = db.scalars(select(StudioIdeaOutcome).where(StudioIdeaOutcome.idea_id == idea.id)
                          .order_by(StudioIdeaOutcome.checkpoint)).all()
    return {
        "id": idea.id, "status": idea.status, "format": idea.format, "topic": idea.topic, "payload": idea.payload,
        "created_at": idea.created_at.isoformat() if idea.created_at else None,
        "planned_date": idea.planned_date.isoformat() if idea.planned_date else None,
        "published_video_id": idea.published_video_id,
        "published_at": idea.published_at.isoformat() if idea.published_at else None,
        "baseline": idea.baseline,
        "outcomes": [{"checkpoint": o.checkpoint, "verdict": o.verdict, "ratio": o.ratio,
                      "views_per_day": o.views_per_day, "baseline_vpd": o.baseline_vpd} for o in outcomes],
    }


@router.get("/ideas/{connection_id}")
def read_ideas(connection_id: int, status: str | None = None, db: Session = Depends(get_db)) -> list[dict]:
    _connection(db, connection_id)
    query = select(StudioIdea).where(StudioIdea.connection_id == connection_id)
    if status:
        query = query.where(StudioIdea.status == status)
    return [_idea_out(db, i) for i in db.scalars(query.order_by(StudioIdea.id.desc())).all()]


@router.post("/ideas/{connection_id}/generate")
def create_ideas(connection_id: int, force: bool = False, db: Session = Depends(get_db)) -> list[dict]:
    connection = _connection(db, connection_id)
    ai = AIClient(db)

    def run():
        if not ai.configured:
            raise AINotConfigured("AI is not configured: add AI_API_KEY (and AI_BASE_URL / AI_MODEL) to backend/.env.")
        return generate_ideas(db, connection, ai, _pack(db, connection), force=force)

    return [_idea_out(db, i) for i in _run_ai(run)]


class IdeaPatch(BaseModel):
    status: str | None = None
    planned_date: date | None = None


def _idea(db: Session, idea_id: int) -> StudioIdea:
    idea = db.get(StudioIdea, idea_id)
    if idea is None:
        raise HTTPException(status_code=404, detail="Idea not found.")
    return idea


@router.patch("/ideas/item/{idea_id}")
def update_idea(idea_id: int, payload: IdeaPatch, db: Session = Depends(get_db)) -> dict:
    idea = _idea(db, idea_id)
    try:
        if payload.planned_date is not None:
            planner.schedule(db, idea, payload.planned_date)
        if payload.status is not None and payload.status != idea.status:
            planner.set_status(db, idea, payload.status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return _idea_out(db, idea)


class PublishedPayload(BaseModel):
    youtube_video_id: str


@router.post("/ideas/item/{idea_id}/published")
def idea_published(idea_id: int, payload: PublishedPayload, db: Session = Depends(get_db)) -> dict:
    idea = _idea(db, idea_id)
    try:
        planner.mark_published(db, idea, payload.youtube_video_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return _idea_out(db, idea)


@router.get("/ideas/{connection_id}/what-worked")
def read_what_worked(connection_id: int, db: Session = Depends(get_db)) -> dict:
    return planner.what_worked(db, _connection(db, connection_id))
