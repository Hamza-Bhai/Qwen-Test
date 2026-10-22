"""Pydantic schemas for channels and videos (API DTOs)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ChannelCreate(BaseModel):
    """Add a channel by handle or channel ID, e.g. '@mkbhd' or 'UC...'.

    The backend resolves it against the YouTube Data API; no client-supplied
    channel statistics are ever accepted (rule #10).
    """

    identifier: str = Field(
        min_length=2,
        max_length=100,
        description="YouTube handle ('@name'), channel ID ('UC...'), or plain handle.",
    )


class ChannelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    youtube_channel_id: str
    title: str
    description: str | None = None
    thumbnail_url: str | None = None
    country: str | None = None
    subscriber_count: int | None = None
    video_count: int | None = None
    view_count: int | None = None
    channel_created_at: datetime | None = None
    last_synced_at: datetime | None = None


class VideoRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    youtube_video_id: str
    title: str
    thumbnail_url: str | None = None
    published_at: datetime | None = None
    duration_seconds: int | None = None
    view_count: int
    like_count: int | None = None
    comment_count: int | None = None
    last_synced_at: datetime | None = None


class SyncResult(BaseModel):
    channel_id: int
    videos_upserted: int
    quota_units_used: int
