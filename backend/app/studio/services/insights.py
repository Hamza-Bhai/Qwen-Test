"""Competitor insights built from stored rows (no API calls, no quota).

Everything here is derived from public data the Data API returned and from the
fair-comparison rules in ``app.studio.benchmarks``.
"""

from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Video
from app.studio import benchmarks as bm
from app.studio.benchmarks import VideoStat
from app.studio.models import StudioCompetitor, StudioCompetitorVideo
from app.studio.services.competitors import list_competitors
from app.studio.services.own_channel import get_own_channel

DEFAULT_NEW_UPLOAD_DAYS = 7


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _settled(live_broadcast_content: str | None) -> bool:
    """Premieres and live streams have no settled numbers yet."""
    return live_broadcast_content in (None, "none")


def _stat(v: StudioCompetitorVideo | Video) -> VideoStat:
    return VideoStat(
        youtube_video_id=v.youtube_video_id,
        title=v.title or "",
        published_at=v.published_at,
        view_count=v.view_count or 0,
        is_short=v.is_short,
        tags=list(v.tags or []),
        hashtags=list(getattr(v, "hashtags", None) or []),
    )


def _competitor_stats(db: Session, comp: StudioCompetitor) -> list[VideoStat]:
    return [_stat(v) for v in comp.videos if _settled(v.live_broadcast_content)]


def _pairs(counted: list[tuple[str, int]]) -> list[list]:
    return [[k, c] for k, c in counted]


def _patterns(stats: list[VideoStat]) -> dict | None:
    p = bm.title_patterns(stats)
    if p is None:
        return None
    return {**asdict(p), "top_keywords": _pairs(p.top_keywords)}


def _owner_stats(db: Session, channel_id: int) -> list[VideoStat]:
    rows = db.scalars(select(Video).where(Video.channel_id == channel_id, Video.removed_at.is_(None))).all()
    return [_stat(v) for v in rows if _settled(v.live_broadcast_content)]


def build_insights(db: Session, now: datetime | None = None) -> dict:
    now = now or _now()
    competitors = list_competitors(db, "watching")
    per_competitor = []
    all_stats: list[VideoStat] = []
    compare_inputs: list[tuple[list[VideoStat], int | None]] = []

    for comp in competitors:
        stats = _competitor_stats(db, comp)
        all_stats.extend(stats)
        compare_inputs.append((stats, comp.subscriber_count))
        result = bm.find_breakouts(stats, now)
        per_competitor.append({
            "id": comp.id,
            "title": comp.title,
            "subscriber_count": comp.subscriber_count,
            "videos_analyzed": len(stats),
            "breakouts": [asdict(b) for b in result.breakouts],
            "breakout_status": result.status,
            "breakout_note": result.note,
            "upload_frequency": asdict(bm.upload_frequency(stats, now)),
            "format_split": bm.format_split(stats),
            "title_patterns": _patterns(stats),
            "top_tags": _pairs(bm.recurring([v.tags for v in stats])),
            "top_hashtags": _pairs(bm.recurring([v.hashtags for v in stats])),
            "breakout_publish_times": bm.breakout_publish_times(result.breakouts),
        })

    own = get_own_channel(db)
    compare = None
    if own is not None:
        table = bm.compare_with_me(_owner_stats(db, own.id), own.subscriber_count, compare_inputs, now)
        compare = {fmt: asdict(c) for fmt, c in table.items()}

    return {
        "competitors": per_competitor,
        "watchlist": {
            "top_tags": _pairs(bm.recurring([v.tags for v in all_stats])),
            "top_hashtags": _pairs(bm.recurring([v.hashtags for v in all_stats])),
            "title_patterns": _patterns(all_stats),
            "format_split": bm.format_split(all_stats),
        },
        "compare": compare,
        "own_channel": {"id": own.id, "title": own.title} if own else None,
    }


def new_uploads(db: Session, now: datetime | None = None) -> list[dict]:
    """Videos published since the owner last looked (or in the last 7 days)."""
    now = now or _now()
    out: list[dict] = []
    for comp in list_competitors(db, "watching"):
        since = _aware(comp.last_viewed_at) if comp.last_viewed_at else now - timedelta(days=DEFAULT_NEW_UPLOAD_DAYS)
        stats = _competitor_stats(db, comp)
        early = {b.youtube_video_id: b.multiple for b in bm.early_breakouts(stats, now)}
        for v in comp.videos:
            if v.published_at is None or not _settled(v.live_broadcast_content):
                continue
            published = _aware(v.published_at)
            if published <= since or published > now:
                continue
            out.append({
                "competitor_id": comp.id,
                "competitor_title": comp.title,
                "youtube_video_id": v.youtube_video_id,
                "title": v.title,
                "published_at": published,
                "view_count": v.view_count or 0,
                "early_breakout": v.youtube_video_id in early,
                "early_multiple": early.get(v.youtube_video_id),
            })
    out.sort(key=lambda u: u["published_at"], reverse=True)
    return out


def mark_seen(db: Session, now: datetime | None = None) -> int:
    now = now or _now()
    competitors = list_competitors(db, "watching")
    for comp in competitors:
        comp.last_viewed_at = now
    db.commit()
    return len(competitors)
