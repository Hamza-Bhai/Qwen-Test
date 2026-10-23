"""Deterministic analytics on stored public data.

Kept deliberately separate from AI-generated recommendations (rule #9):
* this module contains pure math over real, synced numbers only;
* future AI recommendation code will *read* these metrics but never overwrite
  them, and will live under ``app.services.ai``.

Phase 1 ships the module skeleton with basic descriptive metrics computed from
genuinely synced videos — no fabricated inputs, empty input yields empty output.
"""

from dataclasses import dataclass
from datetime import datetime
from statistics import mean

from sqlalchemy.orm import Session

from app.analytics.audit import load_analysis_set
from app.db.models import Channel


@dataclass
class ChannelSummary:
    channel_id: int
    youtube_channel_id: str
    title: str
    videos_analyzed: int
    total_views: int
    avg_views: float | None
    median_views: float | None
    avg_likes: float | None
    like_to_view_ratio: float | None
    avg_duration_seconds: float | None


def summarize_channel(db: Session, channel: Channel, now: datetime | None = None) -> ChannelSummary:
    # Same analysis set as the audit (rolling window), not every stored video.
    videos, _ = load_analysis_set(db, channel, now=now)

    views = [v.view_count for v in videos if v.view_count is not None]
    likes = [v.like_count for v in videos if v.like_count is not None]
    durations = [v.duration_seconds for v in videos if v.duration_seconds is not None]

    avg_views = mean(views) if views else None
    sorted_views = sorted(views)
    if sorted_views:
        n = len(sorted_views)
        mid = n // 2
        median_views = (
            sorted_views[mid] if n % 2 else (sorted_views[mid - 1] + sorted_views[mid]) / 2
        )
    else:
        median_views = None

    total_views = sum(views)
    total_likes = sum(likes)
    ratio = (total_likes / total_views) if total_views and likes else None

    return ChannelSummary(
        channel_id=channel.id,
        youtube_channel_id=channel.youtube_channel_id,
        title=channel.title,
        videos_analyzed=len(videos),
        total_views=total_views,
        avg_views=avg_views,
        median_views=median_views,
        avg_likes=mean(likes) if likes else None,
        like_to_view_ratio=ratio,
        avg_duration_seconds=mean(durations) if durations else None,
    )
