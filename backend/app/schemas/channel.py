"""Pydantic schemas for channels and videos (API DTOs)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ChannelCreate(BaseModel):
    """Add a channel by URL, handle or channel ID.

    Examples: 'https://www.youtube.com/@mkbhd',
    'https://youtube.com/channel/UC...', '@mkbhd', 'UC...'.
    Legacy /c/ and /user/ URLs are detected and reported as unsupported.

    The backend resolves it against the YouTube Data API; no client-supplied
    channel statistics are ever accepted (rule #10).
    """

    identifier: str = Field(
        min_length=2,
        max_length=300,
        description="YouTube channel URL, handle ('@name'), or channel ID ('UC...').",
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


class ChannelAnalysisRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    channel_id: int
    created_at: datetime
    trigger: str
    subscriber_count: int | None = None
    video_count: int | None = None
    view_count: int | None = None
    videos_synced: int
    quota_units_used: int


class ChannelOverview(BaseModel):
    """Everything the Phase-1 dashboard needs in one call."""

    channel: ChannelRead
    summary: dict
    recent_videos: list[VideoRead]
    history: list[ChannelAnalysisRead]
