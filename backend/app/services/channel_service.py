"""Business logic for tracked channels (add / sync / query).

All YouTube interaction goes through ``YouTubeService`` so caching and quota
tracking apply uniformly. This module only persists data that the API actually
returned (rule #10).
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Channel, ChannelAnalysis, Video
from app.services.cache import ResponseCache
from app.services.quota import QuotaTracker
from app.services.url_resolver import parse_channel_reference
from app.services.youtube import (
    SHORTS_MAX_SECONDS,
    YouTubeService,
    channel_payload_to_fields,
    check_is_short,
    video_payload_to_fields,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def get_service(db: Session) -> YouTubeService:
    return YouTubeService(ResponseCache(db), QuotaTracker(db))


def add_channel(db: Session, identifier: str) -> Channel:
    """Parse a channel URL/handle/ID, resolve it against the Data API, store it.

    ``identifier`` may be:
      * a full channel URL (youtube.com/@handle or /channel/UC...)
      * a bare @handle or UC... channel ID
    Legacy /c/ and /user/ URLs raise ``UnsupportedURLError`` (a ValueError
    subclass) with an actionable message — we never guess (Phase 1 rule).
    """
    target = parse_channel_reference(identifier)
    item = get_service(db).resolve_channel(target.identifier)
    fields = channel_payload_to_fields(item)

    channel = db.execute(
        select(Channel).where(Channel.youtube_channel_id == fields["youtube_channel_id"])
    ).scalar_one_or_none()
    if channel is None:
        channel = Channel()
        db.add(channel)

    channel.youtube_channel_id = fields["youtube_channel_id"]
    channel.title = fields["title"] or channel.youtube_channel_id
    channel.description = fields["description"]
    channel.thumbnail_url = fields["thumbnail_url"]
    channel.country = fields["country"]
    channel.uploads_playlist_id = fields["uploads_playlist_id"]
    channel.subscriber_count = fields["subscriber_count"]
    channel.video_count = fields["video_count"]
    channel.view_count = fields["view_count"]
    channel.channel_created_at = _parse_dt(fields["channel_created_at"])
    channel.last_synced_at = _utcnow()

    db.commit()
    db.refresh(channel)
    return channel


def _resolve_shorts(videos: list[Video]) -> None:
    """Set ``is_short`` for each video. Longer than 3 min => never a Short;
    shorter ones are checked once via youtube.com/shorts/<id> (a video's
    format never changes, so a known answer is kept)."""
    to_check = []
    for video in videos:
        if video.live_broadcast_content not in (None, "none"):
            continue  # premiere / live: not analyzed, re-checked once it ends
        if video.duration_seconds is not None and video.duration_seconds > SHORTS_MAX_SECONDS:
            video.is_short = False
        elif video.is_short is None:
            to_check.append(video)
    if not to_check:
        return
    with httpx.Client(timeout=5.0) as http, ThreadPoolExecutor(max_workers=8) as pool:
        results = pool.map(lambda v: check_is_short(v.youtube_video_id, http), to_check)
        for video, is_short in zip(to_check, results):
            video.is_short = is_short


def sync_channel(db: Session, channel: Channel) -> dict:
    """Refresh channel stats + upsert its recent videos. Returns job detail dict."""
    service = get_service(db)
    # A sync must fetch live data: a cached payload would be stamped with a
    # fresh last_synced_at even though it may be minutes old.
    service.bypass_cache = True
    used_before = service.quota.used_today()

    item = service.resolve_channel(channel.youtube_channel_id)
    fields = channel_payload_to_fields(item)
    channel.title = fields["title"] or channel.title
    channel.description = fields["description"]
    channel.thumbnail_url = fields["thumbnail_url"]
    channel.country = fields["country"]
    channel.uploads_playlist_id = fields["uploads_playlist_id"] or channel.uploads_playlist_id
    channel.subscriber_count = fields["subscriber_count"]
    channel.video_count = fields["video_count"]
    channel.view_count = fields["view_count"]
    channel.channel_created_at = _parse_dt(fields["channel_created_at"]) or channel.channel_created_at

    upserted = 0
    synced: list[Video] = []
    playlist_id = channel.uploads_playlist_id
    if playlist_id:
        for v_item in service.fetch_recent_videos(playlist_id):
            vf = video_payload_to_fields(v_item)
            video = db.execute(
                select(Video).where(Video.youtube_video_id == vf["youtube_video_id"])
            ).scalar_one_or_none()
            if video is None:
                video = Video(channel_id=channel.id)
                db.add(video)
            video.youtube_video_id = vf["youtube_video_id"]
            video.title = vf["title"]
            video.description = vf["description"]
            video.thumbnail_url = vf["thumbnail_url"]
            video.published_at = _parse_dt(vf["published_at"])
            video.duration_seconds = vf["duration_seconds"]
            video.live_broadcast_content = vf["live_broadcast_content"]
            video.tags = vf["tags"]
            video.category_id = vf["category_id"]
            video.view_count = vf["view_count"]
            video.like_count = vf["like_count"]
            video.comment_count = vf["comment_count"]
            video.last_synced_at = _utcnow()
            synced.append(video)
            upserted += 1
        _resolve_shorts(synced)

    channel.last_synced_at = _utcnow()
    db.commit()
    return {
        "channel": channel.youtube_channel_id,
        "videos_upserted": upserted,
        "quota_units_used": service.quota.used_today() - used_before,
    }


def list_channels(db: Session) -> list[Channel]:
    return list(
        db.execute(select(Channel).order_by(Channel.created_at.desc())).scalars().all()
    )


def get_channel_by_pk(db: Session, channel_pk: int) -> Channel | None:
    return db.get(Channel, channel_pk)


def list_videos(db: Session, channel_pk: int, *, limit: int = 100) -> list[Video]:
    return list(
        db.execute(
            select(Video)
            .where(Video.channel_id == channel_pk)
            .order_by(Video.published_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )


def delete_channel(db: Session, channel: Channel) -> None:
    db.delete(channel)
    db.commit()


def _record_history(db: Session, channel: Channel, trigger: str, detail: dict) -> ChannelAnalysis:
    """One ChannelAnalysis row for one explicit user action (analyze / sync)."""
    from dataclasses import asdict
    from app.analytics.metrics import summarize_channel

    summary = summarize_channel(db, channel)
    run = ChannelAnalysis(
        channel_id=channel.id,
        trigger=trigger,
        subscriber_count=channel.subscriber_count,
        video_count=channel.video_count,
        view_count=channel.view_count,
        videos_synced=detail["videos_upserted"],
        quota_units_used=detail["quota_units_used"],
        summary=asdict(summary),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def analyze_channel(db: Session, identifier: str) -> tuple[Channel, dict]:
    """Full Phase-1 'Analyze' flow: parse URL -> resolve via API -> sync videos
    -> store one 'analyze' history row. Returns (channel, detail)."""
    channel = add_channel(db, identifier)  # upserts fresh stats from the API
    detail = sync_channel(db, channel)
    run = _record_history(db, channel, "analyze", detail)
    return channel, {**detail, "analysis_id": run.id}


def sync_channel_by_user(db: Session, channel: Channel) -> dict:
    """Explicit 'Sync' button: refresh data and store one 'sync' history row.
    Scheduled syncs call sync_channel directly and are logged as JobRuns."""
    detail = sync_channel(db, channel)
    run = _record_history(db, channel, "sync", detail)
    return {**detail, "analysis_id": run.id}


def list_analyses(db: Session, channel_pk: int, *, limit: int = 50) -> list[ChannelAnalysis]:
    return list(
        db.execute(
            select(ChannelAnalysis)
            .where(ChannelAnalysis.channel_id == channel_pk)
            .order_by(ChannelAnalysis.created_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
