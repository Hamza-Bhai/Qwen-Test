"""Refresh watched competitors from the official YouTube Data API.

Only public data the API returns is stored. Each sync also writes one stats
snapshot per video so view velocity can be measured later.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.channel_service import _resolve_shorts, get_service
from app.services.youtube import YouTubeService, channel_payload_to_fields, video_payload_to_fields
from app.studio.models import StudioCompetitor, StudioCompetitorVideo, StudioCompetitorVideoSnapshot
from app.studio.services.competitors import list_competitors
from app.studio.text import extract_hashtags

logger = logging.getLogger(__name__)

# Quota guard: a competitor needs fewer videos than the owner's full audit.
SYNC_WINDOW_DAYS = 90
SYNC_MIN_VIDEOS = 20
SYNC_MAX_VIDEOS = 100


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def sync_competitor(db: Session, competitor: StudioCompetitor, *, service: YouTubeService | None = None) -> dict:
    service = service or get_service(db)
    # A sync must fetch live data so "last synced" is truthful.
    service.bypass_cache = True
    used_before = service.quota.used_today()

    fields = channel_payload_to_fields(service.resolve_channel(competitor.youtube_channel_id))
    competitor.title = fields["title"] or competitor.title
    competitor.description = fields["description"]
    competitor.thumbnail_url = fields["thumbnail_url"]
    competitor.country = fields["country"]
    competitor.uploads_playlist_id = fields["uploads_playlist_id"] or competitor.uploads_playlist_id
    competitor.subscriber_count = fields["subscriber_count"]  # None when hidden
    competitor.video_count = fields["video_count"]
    competitor.view_count = fields["view_count"]

    synced: list[StudioCompetitorVideo] = []
    if competitor.uploads_playlist_id:
        items = service.fetch_recent_videos(
            competitor.uploads_playlist_id,
            window_days=SYNC_WINDOW_DAYS,
            min_videos=SYNC_MIN_VIDEOS,
            max_videos=SYNC_MAX_VIDEOS,
        )
        now = _utcnow()
        for item in items:
            vf = video_payload_to_fields(item)
            if not vf["youtube_video_id"]:
                continue
            video = db.scalars(
                select(StudioCompetitorVideo).where(
                    StudioCompetitorVideo.youtube_video_id == vf["youtube_video_id"]
                )
            ).first()
            if video is None:
                video = StudioCompetitorVideo(youtube_video_id=vf["youtube_video_id"], first_seen_at=now)
                db.add(video)
            video.competitor_id = competitor.id
            video.title = vf["title"] or ""
            video.description = vf["description"]
            video.thumbnail_url = vf["thumbnail_url"]
            video.published_at = _parse_dt(vf["published_at"])
            video.duration_seconds = vf["duration_seconds"]
            video.live_broadcast_content = vf["live_broadcast_content"]
            video.tags = vf["tags"]
            video.hashtags = extract_hashtags(video.title, video.description)
            video.view_count = vf["view_count"]
            video.like_count = vf["like_count"]
            video.comment_count = vf["comment_count"]
            video.last_synced_at = now
            video.snapshots.append(
                StudioCompetitorVideoSnapshot(
                    captured_at=now,
                    view_count=video.view_count,
                    like_count=video.like_count,
                    comment_count=video.comment_count,
                )
            )
            synced.append(video)
        _resolve_shorts(synced)  # duck-typed: reads/sets duration, live flag, is_short

    competitor.last_synced_at = _utcnow()
    db.commit()
    return {
        "competitor": competitor.youtube_channel_id,
        "videos_upserted": len(synced),
        "quota_units_used": service.quota.used_today() - used_before,
    }


def sync_all_watching(db: Session) -> list[dict]:
    """Sync every ``watching`` competitor; one failure never stops the others."""
    results: list[dict] = []
    for competitor in list_competitors(db, "watching"):
        channel_id = competitor.youtube_channel_id
        try:
            results.append(sync_competitor(db, competitor))
        except Exception as exc:  # noqa: BLE001 - keep the other channels going
            db.rollback()
            logger.warning("Competitor sync failed for %s: %s", channel_id, exc)
            results.append({"competitor": channel_id, "error": str(exc)})
    return results
