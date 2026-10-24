"""Pull a connected channel's private analytics (YouTube Analytics API v2) into time-series tables.

Columns are always read by their ``columnHeaders`` name, never by position.
Re-running a pull upserts: overlapping windows never create duplicate rows.
"""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.studio.models import (
    StudioChannelDaily,
    StudioGoogleConnection,
    StudioMyVideo,
    StudioRetention,
    StudioTrafficDaily,
    StudioVideoTotals,
)

REPORTS = "https://youtubeanalytics.googleapis.com/v2/reports"
FIRST_PULL_DAYS = 90
OVERLAP_DAYS = 3  # Analytics numbers settle over a few days
LIFETIME_START = date(2005, 2, 14)
RETENTION_MIN_AGE_DAYS = 3
RETENTION_REFRESH_DAYS = 60  # older curves barely move; pulled once

DAILY_METRICS = {
    "views": "views",
    "estimatedMinutesWatched": "watch_minutes",
    "averageViewDuration": "avg_view_duration_s",
    "averageViewPercentage": "avg_view_pct",
    "subscribersGained": "subs_gained",
    "subscribersLost": "subs_lost",
    "likes": "likes",
    "comments": "comments",
    "shares": "shares",
}
VIDEO_METRICS = {
    "views": "views",
    "estimatedMinutesWatched": "watch_minutes",
    "averageViewDuration": "avg_view_duration_s",
    "averageViewPercentage": "avg_view_pct",
    "subscribersGained": "subs_gained",
}


def _rows(table: dict) -> list[dict]:
    names = [h["name"] for h in table.get("columnHeaders") or []]
    return [dict(zip(names, row)) for row in table.get("rows") or []]


def _query(session, start: date, end: date, **params) -> list[dict]:
    return _rows(session.get(REPORTS, {
        "ids": "channel==MINE", "startDate": start.isoformat(), "endDate": end.isoformat(), **params,
    }))


def _metric_values(row: dict, metrics: dict[str, str]) -> dict:
    values = {}
    for api_name, column in metrics.items():
        raw = row.get(api_name) or 0
        values[column] = float(raw) if column == "avg_view_pct" else int(raw)
    return values


def pull_window(db: Session, connection: StudioGoogleConnection, today: date) -> tuple[date, date]:
    end = today - timedelta(days=1)
    latest = db.scalar(
        select(func.max(StudioChannelDaily.day)).where(StudioChannelDaily.connection_id == connection.id)
    )
    start = today - timedelta(days=FIRST_PULL_DAYS) if latest is None else latest - timedelta(days=OVERLAP_DAYS)
    return min(start, end), end


def _upsert(db: Session, model, keys: dict, values: dict) -> None:
    row = db.scalars(select(model).filter_by(**keys)).first()
    if row is None:
        db.add(model(**keys, **values))
    else:
        for name, value in values.items():
            setattr(row, name, value)


def pull_channel_daily(db: Session, connection: StudioGoogleConnection, session, start: date, end: date) -> int:
    rows = _query(session, start, end, dimensions="day", metrics=",".join(DAILY_METRICS))
    for row in rows:
        _upsert(db, StudioChannelDaily, {"connection_id": connection.id, "day": date.fromisoformat(row["day"])},
                _metric_values(row, DAILY_METRICS))
    db.commit()
    return len(rows)


def pull_traffic(db: Session, connection: StudioGoogleConnection, session, start: date, end: date) -> int:
    rows = _query(session, start, end, dimensions="day,insightTrafficSourceType",
                  metrics="views,estimatedMinutesWatched")
    for row in rows:
        _upsert(
            db, StudioTrafficDaily,
            {"connection_id": connection.id, "day": date.fromisoformat(row["day"]),
             "source": row["insightTrafficSourceType"]},
            {"views": int(row.get("views") or 0), "watch_minutes": int(row.get("estimatedMinutesWatched") or 0)},
        )
    db.commit()
    return len(rows)


def pull_video_totals(db: Session, connection: StudioGoogleConnection, session, today: date) -> int:
    rows = _query(session, LIFETIME_START, today - timedelta(days=1), dimensions="video",
                  metrics=",".join(VIDEO_METRICS), sort="-views", maxResults=200)
    for row in rows:
        _upsert(db, StudioVideoTotals,
                {"connection_id": connection.id, "youtube_video_id": row["video"], "as_of": today},
                _metric_values(row, VIDEO_METRICS))
    db.commit()
    return len(rows)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def pull_retention(db: Session, connection: StudioGoogleConnection, session, today: date, *, limit: int = 10) -> int:
    now = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)
    candidates = db.scalars(
        select(StudioMyVideo)
        .where(
            StudioMyVideo.connection_id == connection.id,
            StudioMyVideo.is_short.is_(False),
            StudioMyVideo.published_at.is_not(None),
        )
        .order_by(StudioMyVideo.published_at.desc())
    ).all()
    mature = [
        v for v in candidates
        if v.live_broadcast_content in (None, "none")
        and now - _aware(v.published_at) >= timedelta(days=RETENTION_MIN_AGE_DAYS)
    ][:limit]
    pulled = 0
    for video in mature:
        has_rows = db.scalar(
            select(func.count()).select_from(StudioRetention).where(
                StudioRetention.connection_id == connection.id,
                StudioRetention.youtube_video_id == video.youtube_video_id,
            )
        )
        if has_rows and now - _aware(video.published_at) > timedelta(days=RETENTION_REFRESH_DAYS):
            continue
        rows = _query(session, LIFETIME_START, today - timedelta(days=1), dimensions="elapsedVideoTimeRatio",
                      metrics="audienceWatchRatio", filters=f"video=={video.youtube_video_id}")
        db.execute(delete(StudioRetention).where(
            StudioRetention.connection_id == connection.id,
            StudioRetention.youtube_video_id == video.youtube_video_id,
        ))
        pulled_at = datetime.now(timezone.utc)
        db.add_all(
            StudioRetention(
                connection_id=connection.id, youtube_video_id=video.youtube_video_id,
                elapsed_ratio=float(r["elapsedVideoTimeRatio"]), watch_ratio=float(r["audienceWatchRatio"]),
                pulled_at=pulled_at,
            )
            for r in rows
        )
        db.commit()
        pulled += 1
    return pulled
