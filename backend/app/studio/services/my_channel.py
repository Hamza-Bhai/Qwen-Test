"""Connected own channels: run a full pull and build the My channel overview."""

import logging
import threading
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Channel
from app.studio.diagnosis import DayRow, ReachRow, TrafficRow, VideoRow, compute_progress, diagnose
from app.studio.models import (
    StudioChannelDaily,
    StudioGoogleConnection,
    StudioMyVideo,
    StudioReachDaily,
    StudioRetention,
    StudioTrafficDaily,
    StudioVideoTotals,
)
from app.studio.services.analytics_pull import (
    pull_channel_daily,
    pull_retention,
    pull_traffic,
    pull_video_totals,
    pull_window,
)
from app.studio.services.google_oauth import GoogleAuthError, GoogleSession
from app.studio.services.my_videos import sync_my_videos
from app.studio.services.reporting_pull import ensure_reach_job, pull_reach

logger = logging.getLogger(__name__)

OVERVIEW_DAYS = 90
OVERVIEW_VIDEOS = 50
LOOKBACK_DAYS = 56  # two 28-day windows for the diagnosis


class PullInProgress(RuntimeError):
    pass


_pull_locks: dict[int, threading.Lock] = {}
_pull_locks_guard = threading.Lock()


def pull_lock(connection_id: int) -> threading.Lock:
    """One pull per channel at a time (the first pull after connect, the button and the daily job can overlap)."""
    with _pull_locks_guard:
        return _pull_locks.setdefault(connection_id, threading.Lock())


def list_connections(db: Session) -> list[StudioGoogleConnection]:
    return list(db.scalars(select(StudioGoogleConnection).order_by(StudioGoogleConnection.id)).all())


def pull_connection(db: Session, connection: StudioGoogleConnection, *, session=None, today: date | None = None) -> dict:
    """Pull everything for one channel. ``GoogleAuthError`` propagates; a reach failure is only reported."""
    lock = pull_lock(connection.id)
    if not lock.acquire(blocking=False):
        raise PullInProgress("A pull for this channel is already running — refresh in a minute.")
    try:
        return _pull_connection(db, connection, session=session, today=today)
    finally:
        lock.release()


def _pull_connection(db: Session, connection: StudioGoogleConnection, *, session, today: date | None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    session = session or GoogleSession(db, connection)
    start, end = pull_window(db, connection, today)
    result = {
        "connection_id": connection.id,
        "videos": sync_my_videos(db, connection, session),
        "window": [start.isoformat(), end.isoformat()],
        "channel_days": pull_channel_daily(db, connection, session, start, end),
        "traffic_rows": pull_traffic(db, connection, session, start, end),
        "video_totals": pull_video_totals(db, connection, session, today),
        "retention_videos": pull_retention(db, connection, session, today),
    }
    try:
        ensure_reach_job(db, connection, session)
        result["reach"] = pull_reach(db, connection, session)
    except GoogleAuthError:
        raise
    except Exception as exc:  # noqa: BLE001 - CTR is optional; the rest of the pull stands
        db.rollback()
        logger.warning("Reach pull failed for %s: %s", connection.youtube_channel_id, exc)
        result["reach_error"] = str(exc)
    connection.last_pulled_at = datetime.now(timezone.utc)
    connection.last_error = None
    db.commit()
    return result


def pull_all_connected(db: Session) -> list[dict]:
    results: list[dict] = []
    for connection in list_connections(db):
        if connection.status != "connected":
            continue
        channel_id = connection.youtube_channel_id
        try:
            results.append(pull_connection(db, connection))
        except Exception as exc:  # noqa: BLE001 - keep the other channels going
            db.rollback()
            logger.warning("My-channel pull failed for %s: %s", channel_id, exc)
            if connection.status == "connected":
                connection.last_error = str(exc)
                db.commit()
            results.append({"connection_id": connection.id, "error": str(exc)})
    return results


def _latest_totals(db: Session, connection_id: int) -> dict[str, StudioVideoTotals]:
    latest = db.scalar(select(func.max(StudioVideoTotals.as_of)).where(StudioVideoTotals.connection_id == connection_id))
    if latest is None:
        return {}
    rows = db.scalars(select(StudioVideoTotals).where(
        StudioVideoTotals.connection_id == connection_id, StudioVideoTotals.as_of == latest
    )).all()
    return {r.youtube_video_id: r for r in rows}


def get_overview(db: Session, connection: StudioGoogleConnection, today: date | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    cid = connection.id
    since = today - timedelta(days=max(OVERVIEW_DAYS, LOOKBACK_DAYS))

    daily = db.scalars(select(StudioChannelDaily).where(
        StudioChannelDaily.connection_id == cid, StudioChannelDaily.day >= since
    ).order_by(StudioChannelDaily.day)).all()
    traffic = db.scalars(select(StudioTrafficDaily).where(
        StudioTrafficDaily.connection_id == cid, StudioTrafficDaily.day >= today - timedelta(days=LOOKBACK_DAYS)
    )).all()
    reach = db.scalars(select(StudioReachDaily).where(
        StudioReachDaily.connection_id == cid, StudioReachDaily.day >= today - timedelta(days=LOOKBACK_DAYS)
    )).all()
    my_videos = db.scalars(select(StudioMyVideo).where(StudioMyVideo.connection_id == cid)
                           .order_by(StudioMyVideo.published_at.desc())).all()
    totals = _latest_totals(db, cid)
    retention: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for r in db.scalars(select(StudioRetention).where(StudioRetention.connection_id == cid)
                        .order_by(StudioRetention.elapsed_ratio)).all():
        retention[r.youtube_video_id].append((r.elapsed_ratio, r.watch_ratio))

    day_rows = [DayRow(r.day, r.views, r.watch_minutes, r.avg_view_pct, r.subs_gained, r.subs_lost) for r in daily]
    reach_rows = [ReachRow(r.day, r.youtube_video_id, r.impressions, r.clicks) for r in reach]
    video_rows = [
        VideoRow(v.youtube_video_id, v.title, v.published_at, v.is_short,
                 totals[v.youtube_video_id].views if v.youtube_video_id in totals else 0,
                 totals[v.youtube_video_id].avg_view_pct if v.youtube_video_id in totals else None,
                 totals[v.youtube_video_id].subs_gained if v.youtube_video_id in totals else 0)
        for v in my_videos if v.live_broadcast_content in (None, "none")
    ]
    findings = diagnose(day_rows, video_rows, [TrafficRow(t.day, t.source, t.views) for t in traffic],
                        reach_rows, dict(retention), today)

    last28 = today - timedelta(days=28)
    reach28: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])
    for r in reach:
        if r.day >= last28:
            reach28[r.youtube_video_id][0] += r.impressions
            reach28[r.youtube_video_id][1] += r.clicks
    traffic28: dict[str, int] = defaultdict(int)
    for t in traffic:
        if t.day >= last28:
            traffic28[t.source] += t.views
    traffic_total = sum(traffic28.values())

    def video_entry(v: StudioMyVideo) -> dict:
        t = totals.get(v.youtube_video_id)
        imp, clicks = reach28.get(v.youtube_video_id, (None, None))
        curve = retention.get(v.youtube_video_id)
        return {
            "youtube_video_id": v.youtube_video_id, "title": v.title,
            "published_at": v.published_at.isoformat() if v.published_at else None,
            "is_short": v.is_short,
            "views": t.views if t else None,
            "avg_view_pct": t.avg_view_pct if t else None,
            "impressions_28d": imp,
            "ctr_28d": round(clicks / imp * 100, 2) if imp else None,
            "retention_30": round(min(curve, key=lambda p: abs(p[0] - 0.3))[1], 3) if curve else None,
        }

    public = db.scalars(select(Channel).where(Channel.youtube_channel_id == connection.youtube_channel_id)).first()
    has_reach = db.scalar(select(func.count()).select_from(StudioReachDaily)
                          .where(StudioReachDaily.connection_id == cid))
    return {
        "channel": connection_brief(connection),
        "progress": compute_progress(day_rows, reach_rows, today),
        "daily": [
            {"day": r.day.isoformat(), "views": r.views, "watch_minutes": r.watch_minutes,
             "subs_net": r.subs_gained - r.subs_lost, "avg_view_pct": r.avg_view_pct}
            for r in daily if r.day >= today - timedelta(days=OVERVIEW_DAYS)
        ],
        "traffic": [
            {"source": s, "views": v, "share": round(v / traffic_total * 100, 1)}
            for s, v in sorted(traffic28.items(), key=lambda x: -x[1])
        ],
        "videos": [video_entry(v) for v in my_videos[:OVERVIEW_VIDEOS]],
        "findings": [f.to_dict() for f in findings],
        "public_channel_id": public.id if public else None,
        "reach_status": "ok" if has_reach else ("waiting_for_first_report" if connection.reach_job_id else "no_job"),
    }


def connection_brief(connection: StudioGoogleConnection) -> dict:
    """Public fields only — the refresh token never leaves the backend."""
    return {
        "id": connection.id,
        "youtube_channel_id": connection.youtube_channel_id,
        "title": connection.title,
        "thumbnail_url": connection.thumbnail_url,
        "status": connection.status,
        "last_error": connection.last_error,
        "connected_at": connection.connected_at.isoformat() if connection.connected_at else None,
        "last_pulled_at": connection.last_pulled_at.isoformat() if connection.last_pulled_at else None,
    }
