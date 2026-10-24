"""Planner (idea status, schedule, published video) and outcome tracking against the owner's baseline."""

import re
from collections import defaultdict
from datetime import date, datetime, timezone
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.audit import _topic_tokens
from app.studio.models import StudioGoogleConnection, StudioIdea, StudioIdeaOutcome, StudioMyVideo
from app.studio.services.my_channel import _latest_totals

TRANSITIONS = {
    "new": {"saved", "approved", "rejected"},
    "saved": {"approved", "rejected"},
    "approved": {"scheduled", "rejected"},
    "scheduled": {"approved", "rejected"},
}
SCHEDULABLE = {"new", "saved", "approved", "scheduled"}
CHECKPOINTS = (7, 28)
MATURE_DAYS = 7
BASELINE_MIN_VIDEOS = 5
WORKED, DIDNT = 1.25, 0.8
GROUP_MIN = 3


def set_status(db: Session, idea: StudioIdea, status: str) -> StudioIdea:
    if status not in TRANSITIONS.get(idea.status, set()):
        raise ValueError(f"An idea that is {idea.status} cannot become {status}.")
    idea.status = status
    db.commit()
    return idea


def schedule(db: Session, idea: StudioIdea, day: date) -> StudioIdea:
    if idea.status not in SCHEDULABLE:
        raise ValueError(f"An idea that is {idea.status} cannot be scheduled.")
    idea.status = "scheduled"
    idea.planned_date = day
    db.commit()
    return idea


def _age(published_at: datetime, today: date) -> int:
    published = published_at if published_at.tzinfo else published_at.replace(tzinfo=timezone.utc)
    return (today - published.date()).days


def _baseline(db: Session, connection_id: int, fmt: str, exclude: str, today: date) -> dict:
    totals = _latest_totals(db, connection_id)
    videos = db.scalars(select(StudioMyVideo).where(
        StudioMyVideo.connection_id == connection_id, StudioMyVideo.is_short.is_(fmt == "short"),
        StudioMyVideo.published_at.is_not(None), StudioMyVideo.youtube_video_id != exclude,
    )).all()
    vpds = [totals[v.youtube_video_id].views / _age(v.published_at, today) for v in videos
            if v.youtube_video_id in totals and _age(v.published_at, today) >= MATURE_DAYS]
    return {"format": fmt, "median_vpd": round(median(vpds), 2) if len(vpds) >= BASELINE_MIN_VIDEOS else None,
            "videos": len(vpds)}


def mark_published(db: Session, idea: StudioIdea, youtube_video_id: str, *, today: date | None = None) -> StudioIdea:
    today = today or datetime.now(timezone.utc).date()
    if idea.status in ("rejected", "published"):
        raise ValueError(f"An idea that is {idea.status} cannot be marked as published.")
    video = db.scalars(select(StudioMyVideo).where(
        StudioMyVideo.connection_id == idea.connection_id, StudioMyVideo.youtube_video_id == youtube_video_id,
    )).first()
    if video is None:
        raise ValueError("That is not a video of this channel (pull the channel first if it is brand new).")
    taken = db.scalars(select(StudioIdea).where(
        StudioIdea.published_video_id == youtube_video_id, StudioIdea.id != idea.id,
    )).first()
    if taken is not None:
        raise ValueError(f"That video is already linked to the idea \"{taken.topic}\".")
    idea.status = "published"
    idea.published_video_id = youtube_video_id
    idea.published_at = video.published_at
    idea.baseline = _baseline(db, idea.connection_id, idea.format, youtube_video_id, today)
    db.commit()
    return idea


def _verdict(ratio: float | None) -> str:
    if ratio is None:
        return "no_baseline"
    return "worked" if ratio >= WORKED else "didnt" if ratio <= DIDNT else "neutral"


def update_outcomes(db: Session, connection: StudioGoogleConnection, *, today: date | None = None) -> int:
    today = today or datetime.now(timezone.utc).date()
    totals = _latest_totals(db, connection.id)
    created = 0
    ideas = db.scalars(select(StudioIdea).where(
        StudioIdea.connection_id == connection.id, StudioIdea.status == "published",
        StudioIdea.published_at.is_not(None),
    )).all()
    for idea in ideas:
        if idea.published_video_id not in totals:
            continue  # Analytics has no numbers for it yet
        age = _age(idea.published_at, today)
        done = {o.checkpoint for o in db.scalars(select(StudioIdeaOutcome).where(StudioIdeaOutcome.idea_id == idea.id))}
        for checkpoint in CHECKPOINTS:
            if age < checkpoint or checkpoint in done:
                continue
            vpd = totals[idea.published_video_id].views / age
            base = (idea.baseline or {}).get("median_vpd")
            ratio = round(vpd / base, 2) if base else None
            db.add(StudioIdeaOutcome(idea_id=idea.id, checkpoint=checkpoint, views_per_day=round(vpd, 2),
                                     baseline_vpd=base, ratio=ratio, verdict=_verdict(ratio)))
            created += 1
    db.commit()
    return created


def _pattern(title: str) -> str:
    if "?" in title:
        return "question"
    return "number" if re.search(r"\d", title) else "plain"


def what_worked(db: Session, connection: StudioGoogleConnection) -> dict:
    rows = db.execute(
        select(StudioIdea, StudioIdeaOutcome)
        .join(StudioIdeaOutcome, StudioIdeaOutcome.idea_id == StudioIdea.id)
        .where(StudioIdea.connection_id == connection.id)
        .order_by(StudioIdeaOutcome.checkpoint)
    ).all()
    latest: dict[int, tuple[StudioIdea, StudioIdeaOutcome]] = {}
    for idea, outcome in rows:
        latest[idea.id] = (idea, outcome)  # the 28-day result replaces the 7-day one
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"worked": 0, "neutral": 0, "didnt": 0})
    for idea, outcome in latest.values():
        if outcome.verdict == "no_baseline":
            continue
        topic = (_topic_tokens(idea.topic) or ["other"])[0]
        title = (idea.payload.get("titles") or [""])[0]
        for group in (f"format:{idea.format}", f"topic:{topic}", f"pattern:{_pattern(title)}"):
            counts[group][outcome.verdict] += 1
    groups, too_few = [], []
    for group, c in sorted(counts.items()):
        n = sum(c.values())
        if n >= GROUP_MIN:
            groups.append({"group": group, **c, "n": n})
        else:
            too_few.append(group)
    return {"groups": groups, "too_few": too_few,
            "caveat": "Each result is a single video, so it is noisy; trust groups with several videos."}
