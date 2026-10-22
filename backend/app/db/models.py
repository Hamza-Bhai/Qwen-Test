"""ORM models.

Design notes
------------
* Phase 1 is single-user, but every owned resource already carries a nullable
  ``owner_id`` FK to ``users`` so multi-tenant SaaS support is a data migration,
  not a schema rewrite (architecture rules #4, #6).
* Public YouTube Data API v3 content (channels/videos) is kept strictly separate
  from private OAuth-authorized analytics tables (rule #15). In Phase 1 only the
  *public* side exists; private tables are introduced with the OAuth phase.
* ``api_cache`` and ``quota_usage`` implement reusable caching + quota tracking
  for the YouTube service (rule #8).
"""

from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    """Future multi-user anchor. Single default row in Phase 1."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    channels: Mapped[list["Channel"]] = relationship(back_populates="owner")


class Channel(Base):
    """Public channel metadata fetched from the YouTube Data API v3."""

    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    youtube_channel_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    thumbnail_url: Mapped[str | None] = mapped_column(String(512))
    country: Mapped[str | None] = mapped_column(String(8))
    uploads_playlist_id: Mapped[str | None] = mapped_column(String(64))
    subscriber_count: Mapped[int | None] = mapped_column(Integer)  # public stats
    video_count: Mapped[int | None] = mapped_column(Integer)
    view_count: Mapped[int | None] = mapped_column(Integer)
    channel_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    owner: Mapped["User | None"] = relationship(back_populates="channels")
    videos: Mapped[list["Video"]] = relationship(
        back_populates="channel", cascade="all, delete-orphan"
    )
    analyses: Mapped[list["ChannelAnalysis"]] = relationship(
        back_populates="channel", cascade="all, delete-orphan"
    )


class Video(Base):
    """Public video metadata (snippet + public statistics) from the Data API."""

    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), index=True)
    youtube_video_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text)
    thumbnail_url: Mapped[str | None] = mapped_column(String(512))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    tags: Mapped[list | None] = mapped_column(JSON)
    category_id: Mapped[str | None] = mapped_column(String(16))
    # Public statistics only. Private analytics (CTR, retention, revenue)
    # belong to a separate OAuth-scoped model added in a later phase.
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    like_count: Mapped[int | None] = mapped_column(Integer)
    comment_count: Mapped[int | None] = mapped_column(Integer)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    channel: Mapped["Channel"] = relationship(back_populates="videos")

    __table_args__ = (Index("ix_videos_channel_published", "channel_id", "published_at"),)


class ApiCache(Base):
    """Generic response cache keyed by (service, endpoint, params).

    Lets repeated reads skip YouTube quota while keeping results honest:
    cached payloads are genuine API responses, never fabricated data (rule #10).
    """

    __tablename__ = "api_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service: Mapped[str] = mapped_column(String(32), index=True)  # e.g. "youtube"
    cache_key: Mapped[str] = mapped_column(String(255), index=True)
    response_json: Mapped[dict] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (UniqueConstraint("service", "cache_key", name="uq_api_cache_key"),)


class QuotaUsage(Base):
    """Tracks estimated YouTube Data API quota consumption per UTC day."""

    __tablename__ = "quota_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service: Mapped[str] = mapped_column(String(32), default="youtube")
    day: Mapped[date] = mapped_column(Date, index=True)
    units_used: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (UniqueConstraint("service", "day", name="uq_quota_service_day"),)


class JobRun(Base):
    """Audit log for sync/analysis jobs.

    Long-running operations are structured as named jobs so they can later be
    moved to background workers without changing call sites (rule #14).
    """

    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_name: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|success|failed
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detail: Mapped[dict | None] = mapped_column(JSON)


class ChannelAnalysis(Base):
    """History of channel analyses (one row per analyze/sync run).

    Snapshots the public stats *as returned by the API at that moment* so the
    dashboard can show how a channel changed over time. No derived or invented
    metrics — every column mirrors an actual channels.list response (rule #10).
    """

    __tablename__ = "channel_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    trigger: Mapped[str] = mapped_column(String(32), default="analyze")  # analyze|sync|scheduler
    subscriber_count: Mapped[int | None] = mapped_column(Integer)
    video_count: Mapped[int | None] = mapped_column(Integer)
    view_count: Mapped[int | None] = mapped_column(Integer)
    videos_synced: Mapped[int] = mapped_column(Integer, default=0)
    quota_units_used: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict | None] = mapped_column(JSON)  # deterministic metrics snapshot

    channel: Mapped["Channel"] = relationship(back_populates="analyses")

    __table_args__ = (Index("ix_channel_analyses_channel_created", "channel_id", "created_at"),)
