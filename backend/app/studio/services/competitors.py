"""Competitor watchlist: add, list, change status, notes, remove."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.channel_service import get_service
from app.services.url_resolver import parse_channel_reference
from app.services.youtube import channel_payload_to_fields
from app.studio.models import StudioCompetitor

STATUSES = ("candidate", "watching", "rejected")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def get_competitor(db: Session, competitor_id: int) -> StudioCompetitor | None:
    return db.get(StudioCompetitor, competitor_id)


def list_competitors(db: Session, status: str | None = None) -> list[StudioCompetitor]:
    query = select(StudioCompetitor).order_by(StudioCompetitor.added_at.desc(), StudioCompetitor.id.desc())
    if status is not None:
        query = query.where(StudioCompetitor.status == status)
    return list(db.scalars(query).all())


def add_competitor(
    db: Session,
    identifier: str,
    *,
    notes: str | None = None,
    status: str = "watching",
    source: str = "manual",
    reason: dict | None = None,
) -> StudioCompetitor:
    """Resolve a URL / @handle / UC id through the Data API and store the channel.

    An existing row is refreshed; adding manually (status "watching") also
    brings a rejected or candidate channel back to the watchlist.
    """
    if status not in STATUSES:
        raise ValueError(f"Unknown status '{status}'.")
    target = parse_channel_reference(identifier)
    item = get_service(db).resolve_channel(target.identifier)
    fields = channel_payload_to_fields(item)

    comp = db.scalars(
        select(StudioCompetitor).where(StudioCompetitor.youtube_channel_id == fields["youtube_channel_id"])
    ).first()
    if comp is None:
        comp = StudioCompetitor(
            youtube_channel_id=fields["youtube_channel_id"], title="", status=status, source=source,
            discovery_reason=reason,
        )
        db.add(comp)
    elif status == "watching" and comp.status != "watching":
        comp.status = "watching"
        comp.status_changed_at = _utcnow()

    comp.title = fields["title"] or fields["youtube_channel_id"]
    comp.handle = target.identifier if target.kind == "handle" else (item.get("snippet", {}).get("customUrl") or comp.handle)
    comp.description = fields["description"]
    comp.thumbnail_url = fields["thumbnail_url"]
    comp.country = fields["country"]
    comp.uploads_playlist_id = fields["uploads_playlist_id"]
    comp.subscriber_count = fields["subscriber_count"]
    comp.video_count = fields["video_count"]
    comp.view_count = fields["view_count"]
    comp.channel_created_at = _parse_dt(fields["channel_created_at"])
    if notes is not None:
        comp.notes = notes
    db.commit()
    db.refresh(comp)
    return comp


def set_status(db: Session, competitor_id: int, status: str) -> StudioCompetitor:
    if status not in STATUSES:
        raise ValueError(f"Status must be one of: {', '.join(STATUSES)}.")
    comp = get_competitor(db, competitor_id)
    if comp is None:
        raise ValueError("Unknown competitor.")
    if comp.status != status:
        comp.status = status
        comp.status_changed_at = _utcnow()
        db.commit()
        db.refresh(comp)
    return comp


def update_notes(db: Session, competitor_id: int, notes: str | None) -> StudioCompetitor:
    comp = get_competitor(db, competitor_id)
    if comp is None:
        raise ValueError("Unknown competitor.")
    comp.notes = notes
    db.commit()
    db.refresh(comp)
    return comp


def remove_competitor(db: Session, competitor_id: int) -> None:
    """Take a channel off the watchlist for good: it becomes ``rejected`` (never
    suggested again) and its stored videos are deleted."""
    comp = get_competitor(db, competitor_id)
    if comp is None:
        raise ValueError("Unknown competitor.")
    comp.videos.clear()
    comp.status = "rejected"
    comp.status_changed_at = _utcnow()
    db.commit()
