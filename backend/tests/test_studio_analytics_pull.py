"""Private analytics pull: windows, upserts, header-name mapping, retention selection."""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.studio.models import (
    StudioChannelDaily,
    StudioMyVideo,
    StudioRetention,
    StudioTrafficDaily,
    StudioVideoTotals,
)
from app.studio.services.analytics_pull import (
    REPORTS,
    pull_channel_daily,
    pull_retention,
    pull_traffic,
    pull_video_totals,
    pull_window,
)
from tests.studio_fakes import FakeSession, make_connection, open_db

TODAY = date(2026, 10, 24)
DAILY_METRICS = [
    "views", "estimatedMinutesWatched", "averageViewDuration", "averageViewPercentage",
    "subscribersGained", "subscribersLost", "likes", "comments", "shares",
]


@pytest.fixture()
def db():
    with open_db() as session:
        yield session


def _table(headers, rows):
    return {"columnHeaders": [{"name": h} for h in headers], "rows": rows}


def _daily_row(day, views):
    return [day, views, views * 2, 30, 41.5, 1, 0, 2, 0, 1]


def test_first_window_is_90_days_ending_yesterday(db):
    conn = make_connection(db)
    assert pull_window(db, conn, TODAY) == (TODAY - timedelta(days=90), TODAY - timedelta(days=1))


def test_next_window_overlaps_three_days(db):
    conn = make_connection(db)
    db.add(StudioChannelDaily(connection_id=conn.id, day=date(2026, 10, 20), views=1, watch_minutes=1,
                              avg_view_duration_s=1, avg_view_pct=1.0, subs_gained=0, subs_lost=0,
                              likes=0, comments=0, shares=0))
    db.commit()
    assert pull_window(db, conn, TODAY) == (date(2026, 10, 17), date(2026, 10, 23))


def test_channel_daily_upsert_no_duplicates_on_rerun(db):
    conn = make_connection(db)
    first = FakeSession([(REPORTS, None, _table(["day", *DAILY_METRICS], [_daily_row("2026-10-01", 10)]))])
    second = FakeSession([(REPORTS, None, _table(["day", *DAILY_METRICS],
                                                [_daily_row("2026-10-01", 12), _daily_row("2026-10-02", 5)]))])
    assert pull_channel_daily(db, conn, first, date(2026, 10, 1), date(2026, 10, 1)) == 1
    assert pull_channel_daily(db, conn, second, date(2026, 10, 1), date(2026, 10, 2)) == 2
    rows = {r.day: r for r in db.query(StudioChannelDaily).all()}
    assert len(rows) == 2 and rows[date(2026, 10, 1)].views == 12
    r = rows[date(2026, 10, 1)]
    assert (r.watch_minutes, r.avg_view_duration_s, r.avg_view_pct, r.subs_gained, r.likes, r.shares) == (24, 30, 41.5, 1, 2, 1)
    _, url, params = first.calls[0]
    assert params["ids"] == "channel==MINE" and params["dimensions"] == "day"
    assert params["metrics"] == ",".join(DAILY_METRICS)
    assert (params["startDate"], params["endDate"]) == ("2026-10-01", "2026-10-01")


def test_columns_read_by_header_name(db):
    conn = make_connection(db)
    headers = ["day", *reversed(DAILY_METRICS)]
    row = ["2026-10-01", 1, 0, 2, 0, 1, 41.5, 30, 24, 12]  # same values as _daily_row(.., 12), reversed metrics
    pull_channel_daily(db, conn, FakeSession([(REPORTS, None, _table(headers, [row]))]), TODAY, TODAY)
    r = db.query(StudioChannelDaily).one()
    assert (r.views, r.watch_minutes, r.avg_view_pct, r.shares) == (12, 24, 41.5, 1)


def test_empty_rows_is_ok(db):
    conn = make_connection(db)
    session = FakeSession([(REPORTS, None, {"columnHeaders": [{"name": "day"}]})])
    assert pull_channel_daily(db, conn, session, TODAY, TODAY) == 0
    assert pull_traffic(db, conn, session, TODAY, TODAY) == 0


def test_traffic_upsert(db):
    conn = make_connection(db)
    body = _table(["day", "insightTrafficSourceType", "views", "estimatedMinutesWatched"],
                  [["2026-10-01", "YT_SEARCH", 5, 7], ["2026-10-01", "SUBSCRIBER", 3, 4]])
    for _ in range(2):
        assert pull_traffic(db, conn, FakeSession([(REPORTS, None, body)]), TODAY, TODAY) == 2
    rows = {r.source: r for r in db.query(StudioTrafficDaily).all()}
    assert len(rows) == 2 and rows["YT_SEARCH"].views == 5 and rows["YT_SEARCH"].watch_minutes == 7


def test_video_totals_as_of_today(db):
    conn = make_connection(db)
    headers = ["video", "views", "estimatedMinutesWatched", "averageViewDuration", "averageViewPercentage", "subscribersGained"]
    session = FakeSession([(REPORTS, None, _table(headers, [["v1", 100, 50, 40, 33.3, 2]]))])
    assert pull_video_totals(db, conn, session, TODAY) == 1
    assert pull_video_totals(db, conn, session, TODAY) == 1
    row = db.query(StudioVideoTotals).one()
    assert (row.youtube_video_id, row.as_of, row.views, row.avg_view_pct, row.subs_gained) == ("v1", TODAY, 100, 33.3, 2)
    params = session.calls[0][2]
    assert params["dimensions"] == "video" and params["sort"] == "-views" and params["maxResults"] == 200
    assert params["endDate"] == "2026-10-23"


def _my_video(db, conn, vid, days_old, *, short=False, live="none"):
    db.add(StudioMyVideo(
        connection_id=conn.id, youtube_video_id=vid, title=vid, is_short=short, live_broadcast_content=live,
        published_at=datetime(2026, 10, 24, tzinfo=timezone.utc) - timedelta(days=days_old),
    ))
    db.commit()


def _retention_session():
    curve = _table(["elapsedVideoTimeRatio", "audienceWatchRatio"], [[0.1, 0.9], [0.3, 0.6], [0.5, 0.4]])
    return FakeSession([(REPORTS, None, curve)])


def test_retention_only_long_mature_videos_and_replaces_rows(db):
    conn = make_connection(db)
    _my_video(db, conn, "long_ok", 10)
    _my_video(db, conn, "short", 10, short=True)
    _my_video(db, conn, "too_new", 1)
    _my_video(db, conn, "live", 10, live="live")
    session = _retention_session()
    assert pull_retention(db, conn, session, TODAY) == 1
    assert pull_retention(db, conn, session, TODAY) == 1  # < 60 days old: pulled again, rows replaced
    rows = db.query(StudioRetention).all()
    assert {r.youtube_video_id for r in rows} == {"long_ok"} and len(rows) == 3
    params = session.calls[0][2]
    assert params["filters"] == "video==long_ok" and params["dimensions"] == "elapsedVideoTimeRatio"
    assert params["metrics"] == "audienceWatchRatio"


def test_retention_respects_limit_newest_first(db):
    conn = make_connection(db)
    for i in range(4):
        _my_video(db, conn, f"v{i}", 10 + i)
    session = _retention_session()
    assert pull_retention(db, conn, session, TODAY, limit=2) == 2
    assert [c[2]["filters"] for c in session.calls] == ["video==v0", "video==v1"]


def test_retention_skips_old_video_already_pulled(db):
    conn = make_connection(db)
    _my_video(db, conn, "old", 120)
    session = _retention_session()
    assert pull_retention(db, conn, session, TODAY) == 1
    assert pull_retention(db, conn, session, TODAY) == 0
    assert db.query(StudioRetention).count() == 3
