"""Business logic for tracked channels (add / sync / query).

All YouTube interaction goes through ``YouTubeService`` so caching and quota
tracking apply uniformly. This module only persists data that the API actually
returned (rule #10).
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Channel, Video
from app.services.cache import ResponseCache
from app.services.quota import QuotaTracker
from app.services.youtube import YouTubeService, channel_payload_to_fields, video_payload_to_fields


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
    """Resolve a handle/ID against the Data API and store the channel."""
    item = get_service(db).resolve_channel(identifier)
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


def sync_channel(db: Session, channel: Channel, *, video_limit: int = 50) -> dict:
    """Refresh channel stats + upsert its recent videos. Returns job detail dict."""
    service = get_service(db)
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
    playlist_id = channel.uploads_playlist_id
    if playlist_id:
        for v_item in service.fetch_recent_videos(playlist_id, limit=video_limit):
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
            video.tags = vf["tags"]
            video.category_id = vf["category_id"]
            video.view_count = vf["view_count"]
            video.like_count = vf["like_count"]
            video.comment_count = vf["comment_count"]
            video.last_synced_at = _utcnow()
            upserted += 1

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
