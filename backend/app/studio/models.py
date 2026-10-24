"""Growth Studio tables (all prefixed ``studio_``).

Kept apart from ``app.db.models`` so the public-channel feature is untouched.
"""

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models import utcnow


class StudioSetting(Base):
    """Small key/value store for Studio settings (e.g. niche keywords)."""

    __tablename__ = "studio_setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[list | dict | str | int | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class StudioCompetitor(Base):
    """A channel the owner watches (or that was suggested / rejected)."""

    __tablename__ = "studio_competitor"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    youtube_channel_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    handle: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    thumbnail_url: Mapped[str | None] = mapped_column(String(512))
    country: Mapped[str | None] = mapped_column(String(8))
    uploads_playlist_id: Mapped[str | None] = mapped_column(String(64))
    subscriber_count: Mapped[int | None] = mapped_column(Integer)  # None = hidden by the channel
    video_count: Mapped[int | None] = mapped_column(Integer)
    view_count: Mapped[int | None] = mapped_column(Integer)
    channel_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), index=True)  # candidate | watching | rejected
    source: Mapped[str] = mapped_column(String(16))  # manual | search | related
    discovery_reason: Mapped[dict | None] = mapped_column(JSON)
    notes: Mapped[str | None] = mapped_column(Text)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    videos: Mapped[list["StudioCompetitorVideo"]] = relationship(
        back_populates="competitor", cascade="all, delete-orphan"
    )


class StudioCompetitorVideo(Base):
    __tablename__ = "studio_competitor_video"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    competitor_id: Mapped[int] = mapped_column(ForeignKey("studio_competitor.id"), index=True)
    youtube_video_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text)
    thumbnail_url: Mapped[str | None] = mapped_column(String(512))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    is_short: Mapped[bool | None] = mapped_column(Boolean)
    live_broadcast_content: Mapped[str | None] = mapped_column(String(16))
    tags: Mapped[list | None] = mapped_column(JSON)
    hashtags: Mapped[list | None] = mapped_column(JSON)
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    like_count: Mapped[int | None] = mapped_column(Integer)
    comment_count: Mapped[int | None] = mapped_column(Integer)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    hook_text: Mapped[str | None] = mapped_column(Text)
    hook_segments: Mapped[list | None] = mapped_column(JSON)

    competitor: Mapped["StudioCompetitor"] = relationship(back_populates="videos")
    snapshots: Mapped[list["StudioCompetitorVideoSnapshot"]] = relationship(
        back_populates="video", cascade="all, delete-orphan"
    )


class StudioCompetitorVideoSnapshot(Base):
    """One stats reading per video per sync (for velocity)."""

    __tablename__ = "studio_competitor_video_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("studio_competitor_video.id"), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    like_count: Mapped[int | None] = mapped_column(Integer)
    comment_count: Mapped[int | None] = mapped_column(Integer)

    video: Mapped["StudioCompetitorVideo"] = relationship(back_populates="snapshots")



# --- Milestone C: the owner's connected channels and their private analytics ---------------


def _connection_fk() -> Mapped[int]:
    return mapped_column(ForeignKey("studio_google_connection.id", ondelete="CASCADE"), index=True)


class StudioGoogleConnection(Base):
    """One own channel connected with Google OAuth (several are allowed)."""

    __tablename__ = "studio_google_connection"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    youtube_channel_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    thumbnail_url: Mapped[str | None] = mapped_column(String(512))
    # Stored unencrypted: the DB is local and gitignored. Never logged or returned by the API.
    refresh_token: Mapped[str | None] = mapped_column(Text)
    scopes: Mapped[str] = mapped_column(String(512), default="")
    status: Mapped[str] = mapped_column(String(20), default="connected")  # connected | disconnected | needs_reconnect
    last_error: Mapped[str | None] = mapped_column(Text)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_pulled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reach_job_id: Mapped[str | None] = mapped_column(String(64))
    reach_report_ids: Mapped[list] = mapped_column(JSON, default=list)

    def __repr__(self) -> str:  # keeps the refresh token out of logs and tracebacks
        return f"<StudioGoogleConnection id={self.id} channel={self.youtube_channel_id} status={self.status}>"


class StudioMyVideo(Base):
    """An upload of a connected channel (read with that channel's own token)."""

    __tablename__ = "studio_my_video"
    __table_args__ = (UniqueConstraint("connection_id", "youtube_video_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_id: Mapped[int] = _connection_fk()
    youtube_video_id: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(512))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    is_short: Mapped[bool | None] = mapped_column(Boolean)
    live_broadcast_content: Mapped[str | None] = mapped_column(String(16))


class StudioChannelDaily(Base):
    __tablename__ = "studio_analytics_channel_daily"
    __table_args__ = (UniqueConstraint("connection_id", "day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_id: Mapped[int] = _connection_fk()
    day: Mapped[date] = mapped_column(Date)
    views: Mapped[int] = mapped_column(Integer, default=0)
    watch_minutes: Mapped[int] = mapped_column(Integer, default=0)
    avg_view_duration_s: Mapped[int] = mapped_column(Integer, default=0)
    avg_view_pct: Mapped[float] = mapped_column(Float, default=0.0)
    subs_gained: Mapped[int] = mapped_column(Integer, default=0)
    subs_lost: Mapped[int] = mapped_column(Integer, default=0)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    comments: Mapped[int] = mapped_column(Integer, default=0)
    shares: Mapped[int] = mapped_column(Integer, default=0)


class StudioVideoTotals(Base):
    """Lifetime totals of one video, as read on ``as_of`` (one row per pull day)."""

    __tablename__ = "studio_analytics_video_daily"
    __table_args__ = (UniqueConstraint("connection_id", "youtube_video_id", "as_of"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_id: Mapped[int] = _connection_fk()
    youtube_video_id: Mapped[str] = mapped_column(String(32))
    as_of: Mapped[date] = mapped_column(Date)
    views: Mapped[int] = mapped_column(Integer, default=0)
    watch_minutes: Mapped[int] = mapped_column(Integer, default=0)
    avg_view_duration_s: Mapped[int] = mapped_column(Integer, default=0)
    avg_view_pct: Mapped[float] = mapped_column(Float, default=0.0)
    subs_gained: Mapped[int] = mapped_column(Integer, default=0)


class StudioTrafficDaily(Base):
    __tablename__ = "studio_analytics_traffic"
    __table_args__ = (UniqueConstraint("connection_id", "day", "source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_id: Mapped[int] = _connection_fk()
    day: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(64))
    views: Mapped[int] = mapped_column(Integer, default=0)
    watch_minutes: Mapped[int] = mapped_column(Integer, default=0)


class StudioRetention(Base):
    """Audience retention curve point (elapsedVideoTimeRatio -> audienceWatchRatio)."""

    __tablename__ = "studio_analytics_retention"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_id: Mapped[int] = _connection_fk()
    youtube_video_id: Mapped[str] = mapped_column(String(32), index=True)
    elapsed_ratio: Mapped[float] = mapped_column(Float)
    watch_ratio: Mapped[float] = mapped_column(Float)
    pulled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StudioReachDaily(Base):
    """Impressions and clicks (impressions x CTR) per day and video, from Reporting API files."""

    __tablename__ = "studio_reach_daily"
    __table_args__ = (UniqueConstraint("connection_id", "day", "youtube_video_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_id: Mapped[int] = _connection_fk()
    day: Mapped[date] = mapped_column(Date)
    youtube_video_id: Mapped[str] = mapped_column(String(32))
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[float] = mapped_column(Float, default=0.0)


# --- Milestone D: advisor answers, video ideas and their outcomes ----------------------------


class StudioAdvisorReport(Base):
    """One validated AI reply (advice or ideas), cached by the hash of the evidence it was given."""

    __tablename__ = "studio_advisor_report"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_id: Mapped[int] = _connection_fk()
    kind: Mapped[str] = mapped_column(String(16))  # advice | ideas
    evidence_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    payload: Mapped[dict] = mapped_column(JSON)
    model: Mapped[str] = mapped_column(String(100), default="")


class StudioIdea(Base):
    __tablename__ = "studio_idea"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_id: Mapped[int] = _connection_fk()
    report_id: Mapped[int | None] = mapped_column(ForeignKey("studio_advisor_report.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(16), index=True)  # new | saved | approved | rejected | scheduled | published
    format: Mapped[str] = mapped_column(String(8))  # short | long
    topic: Mapped[str] = mapped_column(String(255))
    payload: Mapped[dict] = mapped_column(JSON)
    planned_date: Mapped[date | None] = mapped_column(Date)
    published_video_id: Mapped[str | None] = mapped_column(String(32))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    baseline: Mapped[dict | None] = mapped_column(JSON)


class StudioIdeaOutcome(Base):
    """Day-7 / day-28 result of a published idea against the owner's baseline at publish time."""

    __tablename__ = "studio_idea_outcome"
    __table_args__ = (UniqueConstraint("idea_id", "checkpoint"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    idea_id: Mapped[int] = mapped_column(ForeignKey("studio_idea.id", ondelete="CASCADE"), index=True)
    checkpoint: Mapped[int] = mapped_column(Integer)  # 7 | 28
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    views_per_day: Mapped[float] = mapped_column(Float)
    baseline_vpd: Mapped[float | None] = mapped_column(Float)
    ratio: Mapped[float | None] = mapped_column(Float)
    verdict: Mapped[str] = mapped_column(String(10))  # worked | neutral | didnt | no_baseline
