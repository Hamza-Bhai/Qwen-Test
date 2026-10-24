"""The advisor: answers the six questions from the evidence pack only, citing evidence ids.

Any reply that cites an id that is not in the pack, skips a question or has the
wrong shape is rejected and nothing is stored. Results are cached per evidence
hash and day, so re-opening the dashboard never spends another AI call.
"""

import json
import threading
from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.studio.evidence import evidence_hash
from app.studio.models import StudioAdvisorReport, StudioGoogleConnection

QUESTIONS = ["why_not_growing", "mistakes", "how_to_fix", "competitors_better", "topics_next", "focus_this_week"]
QUESTION_LABELS = {
    "why_not_growing": "Why is the channel not growing?",
    "mistakes": "What mistakes am I making?",
    "how_to_fix": "How do I fix them?",
    "competitors_better": "What are competitors doing better?",
    "topics_next": "Which topics should I make next?",
    "focus_this_week": "What should I focus on this week?",
}

SYSTEM_PROMPT = """You are a YouTube growth advisor for one small creator.
You get an evidence pack: items with an "id", a "kind" and a "text". Use ONLY this evidence.
Rules:
- Every answer must list the ids it relies on in "evidence_ids" (at least one, only ids from the pack).
- Never invent numbers, channels, videos or facts that are not in the evidence.
- Shorts and long-form videos are different formats: never compare them with each other as if equal.
- If the evidence is too thin for a question, say so plainly and use confidence "low".
- Be concrete and short (at most 6 sentences per answer), plain English, no hype.
Reply with JSON only: {"answers": [{"question": "<question id>", "answer": "...", "evidence_ids": ["..."],
"confidence": "high" | "medium" | "low"}]} with exactly one answer for each question id given."""


class AdviceRejected(ValueError):
    pass


class AdviceInProgress(RuntimeError):
    pass


class _Answer(BaseModel):
    question: str
    answer: str = Field(min_length=1, max_length=1200)
    evidence_ids: list[str] = Field(min_length=1)
    confidence: Literal["high", "medium", "low"]


class _Advice(BaseModel):
    answers: list[_Answer]


_locks: dict[tuple[str, int], threading.Lock] = {}
_locks_guard = threading.Lock()


def report_lock(kind: str, connection_id: int) -> threading.Lock:
    """One generation per channel and kind at a time (button and daily job can overlap)."""
    with _locks_guard:
        return _locks.setdefault((kind, connection_id), threading.Lock())


def pack_for_model(pack: dict) -> dict:
    return {
        "channel": pack["channel"],
        "evidence": [{"id": i["id"], "kind": i["kind"], "text": i["text"]} for i in pack["items"]],
        "notes": pack["notes"],
    }


def check_ids(ids: list[str], known: set[str], where: str) -> None:
    unknown = [i for i in ids if i not in known]
    if unknown:
        raise AdviceRejected(f"The model cited evidence that does not exist ({where}): {', '.join(unknown)}")


def cached_report(db: Session, connection: StudioGoogleConnection, kind: str, digest: str,
                  today: date) -> StudioAdvisorReport | None:
    for report in db.scalars(select(StudioAdvisorReport).where(
        StudioAdvisorReport.connection_id == connection.id, StudioAdvisorReport.kind == kind,
        StudioAdvisorReport.evidence_hash == digest,
    ).order_by(StudioAdvisorReport.id.desc())).all():
        created = report.created_at if report.created_at.tzinfo else report.created_at.replace(tzinfo=timezone.utc)
        if created.date() == today:
            return report
    return None


def _validate(reply: dict, known: set[str]) -> dict:
    try:
        advice = _Advice.model_validate(reply)
    except ValidationError as exc:
        raise AdviceRejected(f"The model's reply had the wrong shape: {exc.errors()[0]['msg']}") from None
    by_question = {}
    for answer in advice.answers:
        if answer.question not in QUESTIONS:
            raise AdviceRejected(f"The model answered an unknown question: {answer.question}")
        check_ids(answer.evidence_ids, known, answer.question)
        by_question[answer.question] = answer
    missing = [q for q in QUESTIONS if q not in by_question]
    if missing:
        raise AdviceRejected(f"The model skipped question(s): {', '.join(missing)}")
    return {"answers": [by_question[q].model_dump() for q in QUESTIONS]}


def generate_advice(db: Session, connection: StudioGoogleConnection, ai, pack: dict, *,
                    today: date | None = None, force: bool = False) -> StudioAdvisorReport:
    today = today or datetime.now(timezone.utc).date()
    digest = evidence_hash(pack)
    lock = report_lock("advice", connection.id)
    if not lock.acquire(blocking=False):
        raise AdviceInProgress("Advice for this channel is already being generated — refresh in a minute.")
    try:
        if not force and (cached := cached_report(db, connection, "advice", digest, today)):
            return cached
        user = json.dumps({**pack_for_model(pack), "questions": QUESTION_LABELS}, ensure_ascii=False)
        reply, model = ai.complete_json(SYSTEM_PROMPT, user)
        payload = _validate(reply, {i["id"] for i in pack["items"]})
        payload["evidence"] = pack_for_model(pack)["evidence"]  # lets the UI show what each id says
        report = StudioAdvisorReport(connection_id=connection.id, kind="advice", evidence_hash=digest,
                                     payload=payload, model=model)
        db.add(report)
        db.commit()
        return report
    finally:
        lock.release()


def latest_advice(db: Session, connection: StudioGoogleConnection) -> StudioAdvisorReport | None:
    return db.scalars(select(StudioAdvisorReport).where(
        StudioAdvisorReport.connection_id == connection.id, StudioAdvisorReport.kind == "advice",
    ).order_by(StudioAdvisorReport.id.desc())).first()
