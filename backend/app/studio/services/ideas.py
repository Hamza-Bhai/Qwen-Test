"""Daily video ideas from the evidence pack, with a duplicate guard and code-decided upload times."""

import json
from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.studio.evidence import evidence_hash, title_tokens
from app.studio.models import StudioAdvisorReport, StudioGoogleConnection, StudioIdea
from app.studio.services.advisor import AdviceRejected, cached_report, check_ids, pack_for_model, report_lock

IDEAS_ASKED = 5
DUPLICATE_OVERLAP = 0.6
NO_TIMING = "no reliable data"
FORMAT_NAMES = {"short": "Shorts", "long": "long-form"}

SYSTEM_PROMPT = """You plan YouTube videos for one small creator, using ONLY the evidence pack you are given.
Return JSON only: {"ideas": [ ... ]} with exactly %d ideas. Each idea:
{"topic": str, "reason": str (why, in 1-2 sentences, from the evidence), "format": "short" | "long",
 "titles": [3 to 5 title options], "hook": str (first 5 seconds), "outline": [3 to 8 beats],
 "duration_minutes": number, "description": str, "hashtags": [up to 8], "tags": [up to 15],
 "competitor_refs": [ids starting with "B:" from the pack, may be empty],
 "evidence_ids": [at least one id from the pack], "confidence": "high" | "medium" | "low", "priority": 1-5}
Rules:
- Never repeat a topic or title the creator already published ("own_titles") or was already given ("earlier_ideas").
- Cite only ids that exist in the pack. Do not invent numbers or competitors.
- Prefer the format the evidence says works for this channel; Shorts and long-form are judged separately.
- Do not suggest an upload day or time; the app decides that from the creator's own data.""" % IDEAS_ASKED


class IdeasRejected(AdviceRejected):
    pass


class IdeasInProgress(RuntimeError):
    pass


class _Idea(BaseModel):
    topic: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1)
    format: Literal["short", "long"]
    titles: list[str] = Field(min_length=3, max_length=5)
    hook: str = Field(min_length=1)
    outline: list[str] = Field(min_length=3, max_length=8)
    duration_minutes: float = Field(gt=0)
    description: str
    hashtags: list[str] = Field(max_length=8)
    tags: list[str] = Field(max_length=15)
    competitor_refs: list[str] = []
    evidence_ids: list[str] = Field(min_length=1)
    confidence: Literal["high", "medium", "low"]
    priority: int = Field(ge=1, le=5)


class _Ideas(BaseModel):
    ideas: list[_Idea] = Field(min_length=1)


def overlap(a: str, b: str) -> float:
    """Share of ``a``'s topic words that also appear in ``b`` (hashtags and stopwords ignored)."""
    words = title_tokens(a)
    return len(words & title_tokens(b)) / len(words) if words else 0.0


def is_duplicate(idea_text: str, existing: list[str]) -> bool:
    return any(overlap(idea_text, other) >= DUPLICATE_OVERLAP for other in existing)


def _upload_time(pack: dict, fmt: str) -> str:
    finding = next((i for i in pack["items"] if i["id"] == "F:publish_timing"), None)
    data = (finding or {}).get("data") or {}
    evidence = data.get("evidence") or {}
    if data.get("status") == "good" and evidence.get("format") == FORMAT_NAMES[fmt] and evidence.get("best_day"):
        return f"{evidence['best_day']} (best day for your {FORMAT_NAMES[fmt]} so far)"
    return NO_TIMING


def _validate(reply: dict, known: set[str]) -> list[_Idea]:
    try:
        ideas = _Ideas.model_validate(reply).ideas
    except ValidationError as exc:
        error = exc.errors()[0]
        where = ".".join(str(p) for p in error["loc"])
        raise IdeasRejected(f"The model's ideas had the wrong shape ({where}: {error['msg']}).") from None
    for n, idea in enumerate(ideas, 1):
        check_ids(idea.evidence_ids + idea.competitor_refs, known, f"idea {n}")
        not_breakouts = [r for r in idea.competitor_refs if not r.startswith("B:")]
        if not_breakouts:
            raise IdeasRejected(f"Competitor references must be breakout videos (B:…), got: {', '.join(not_breakouts)}")
    return ideas


def _ideas_of(db: Session, report: StudioAdvisorReport) -> list[StudioIdea]:
    return list(db.scalars(select(StudioIdea).where(StudioIdea.report_id == report.id).order_by(StudioIdea.id)).all())


def generate_ideas(db: Session, connection: StudioGoogleConnection, ai, pack: dict, *,
                   today: date | None = None, force: bool = False) -> list[StudioIdea]:
    today = today or datetime.now(timezone.utc).date()
    digest = evidence_hash(pack)
    lock = report_lock("ideas", connection.id)
    if not lock.acquire(blocking=False):
        raise IdeasInProgress("Ideas for this channel are already being generated — refresh in a minute.")
    try:
        if not force and (cached := cached_report(db, connection, "ideas", digest, today)):
            return _ideas_of(db, cached)
        user = json.dumps({**pack_for_model(pack), "own_titles": pack["own_titles"],
                           "earlier_ideas": pack["earlier_ideas"]}, ensure_ascii=False)
        reply, model = ai.complete_json(SYSTEM_PROMPT, user)
        try:
            ideas = _validate(reply, {i["id"] for i in pack["items"]})
        except AdviceRejected as exc:
            raise IdeasRejected(str(exc)) from None

        existing = list(pack["own_titles"]) + list(pack["earlier_ideas"])
        accepted: list[dict] = []
        skipped = 0
        for idea in ideas:
            text = f"{idea.topic} {idea.titles[0]}"
            if is_duplicate(text, existing):
                skipped += 1
                continue
            existing.append(text)
            accepted.append({**idea.model_dump(), "upload_time": _upload_time(pack, idea.format)})

        report = StudioAdvisorReport(connection_id=connection.id, kind="ideas", evidence_hash=digest, model=model,
                                     payload={"ideas": accepted, "skipped_duplicates": skipped,
                                              "evidence": pack_for_model(pack)["evidence"]})
        db.add(report)
        db.flush()
        rows = [StudioIdea(connection_id=connection.id, report_id=report.id, status="new", format=i["format"],
                           topic=i["topic"], payload=i) for i in accepted]
        db.add_all(rows)
        db.commit()
        return rows
    finally:
        lock.release()
