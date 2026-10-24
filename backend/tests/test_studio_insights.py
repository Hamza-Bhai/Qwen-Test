"""Insights, new uploads and compare-with-me built from stored rows."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.db.models import Channel, Video
from app.db.session import SessionLocal, init_db
from app.studio.models import StudioCompetitor, StudioCompetitorVideo
from app.studio.services import insights

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)
API = "/api/v1/studio/competitors"


@pytest.fixture()
def db():
    init_db()
    with SessionLocal() as session:
        yield session


@pytest.fixture()
def client():
    from app.main import app

    init_db()
    with TestClient(app) as c:
        yield c


def comp(db, n, *, status="watching", subs=1000, last_viewed=None):
    row = StudioCompetitor(
        youtube_channel_id=f"UC{n:022d}", title=f"Comp {n}", status=status, source="manual",
        subscriber_count=subs, last_viewed_at=last_viewed,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def add_video(db, row, i, days_old, views, *, short=False, live="none", tags=None, title="Sunday reset", desc=None):
    v = StudioCompetitorVideo(
        competitor_id=row.id, youtube_video_id=f"c{row.id}v{i:07d}"[:11].ljust(11, "x"), title=title,
        published_at=NOW - timedelta(days=days_old), view_count=views, is_short=short,
        live_broadcast_content=live, tags=tags, hashtags=[], first_seen_at=NOW, last_synced_at=NOW,
    )
    db.add(v)
    db.commit()
    return v


def seed_channel(db, row, n_mature=12, outlier=True):
    for i in range(n_mature):
        add_video(db, row, i, 10, 1000, tags=["calm", "focus"])  # vpd 100
    if outlier:
        add_video(db, row, 99, 10, 4000, title="Why this worked")  # vpd 400 -> 4x


def test_insights_only_include_watching_competitors(db):
    seed_channel(db, comp(db, 1))
    seed_channel(db, comp(db, 2, status="candidate"))
    seed_channel(db, comp(db, 3, status="rejected"))
    out = insights.build_insights(db, now=NOW)
    assert [c["title"] for c in out["competitors"]] == ["Comp 1"]


def test_breakout_and_status_reported_per_competitor(db):
    seed_channel(db, comp(db, 1))
    seed_channel(db, comp(db, 2), n_mature=5, outlier=False)
    by_title = {c["title"]: c for c in insights.build_insights(db, now=NOW)["competitors"]}
    big = by_title["Comp 1"]
    assert big["breakout_status"] == "ok" and big["videos_analyzed"] == 13
    assert len(big["breakouts"]) == 1 and big["breakouts"][0]["multiple"] == 4.0
    assert big["top_tags"] == [["calm", 12], ["focus", 12]]
    small = by_title["Comp 2"]
    assert small["breakout_status"] == "inconclusive" and small["breakouts"] == []
    assert "5" in small["breakout_note"]
    assert small["upload_frequency"]["reliable"] is False


def test_live_and_upcoming_videos_are_left_out(db):
    row = comp(db, 1)
    seed_channel(db, row, n_mature=2, outlier=False)
    add_video(db, row, 50, 10, 1, live="live")
    add_video(db, row, 51, 10, 1, live="upcoming")
    assert insights.build_insights(db, now=NOW)["competitors"][0]["videos_analyzed"] == 2


def test_hidden_subscribers_do_not_break_insights(db):
    seed_channel(db, comp(db, 1, subs=None))
    out = insights.build_insights(db, now=NOW)
    assert out["competitors"][0]["subscriber_count"] is None


def test_compare_none_without_own_channel(db):
    seed_channel(db, comp(db, 1))
    out = insights.build_insights(db, now=NOW)
    assert out["compare"] is None and out["own_channel"] is None


def _own_channel(db, n_videos=6, vpd=50, removed=0):
    ch = Channel(youtube_channel_id="UC" + "o" * 22, title="Mine", subscriber_count=500)
    db.add(ch)
    db.commit()
    for i in range(n_videos + removed):
        db.add(Video(
            channel_id=ch.id, youtube_video_id=f"own{i:08d}", title="Mine",
            published_at=NOW - timedelta(days=10), view_count=vpd * 10, is_short=False,
            removed_at=NOW if i >= n_videos else None,
        ))
    db.commit()
    return ch


def test_compare_uses_own_channel_videos_and_ignores_removed(db):
    seed_channel(db, comp(db, 1), n_mature=8, outlier=False)
    _own_channel(db, n_videos=6, vpd=50, removed=3)
    out = insights.build_insights(db, now=NOW)
    assert out["own_channel"]["title"] == "Mine"
    long = out["compare"]["long"]
    assert long["owner_videos"] == 6 and long["owner_median_vpd"] == 50.0
    assert long["watchlist_median_vpd"] == 100.0 and long["competitors_counted"] == 1
    assert long["owner_vpd_per_1k_subs"] == 100.0  # 50 / 500 * 1000


def test_new_uploads_since_last_viewed_and_default_7_days(db):
    never = comp(db, 1)
    add_video(db, never, 1, 2, 10, title="Fresh")
    add_video(db, never, 2, 9, 10, title="Old")
    seen = comp(db, 2, last_viewed=NOW - timedelta(days=20))
    add_video(db, seen, 3, 15, 10, title="Newer than last visit")
    add_video(db, seen, 4, 25, 10, title="Before last visit")
    titles = [u["title"] for u in insights.new_uploads(db, now=NOW)]
    assert titles == ["Fresh", "Newer than last visit"]  # newest first; "Old" is beyond 7 days


def test_early_breakout_flag_on_new_upload(db):
    row = comp(db, 1)
    seed_channel(db, row, n_mature=10, outlier=False)  # median 100 vpd
    add_video(db, row, 70, 2, 1200, title="Taking off")   # 600/day = 6x
    add_video(db, row, 71, 2, 200, title="Normal")        # 100/day
    ups = {u["title"]: u for u in insights.new_uploads(db, now=NOW)}
    assert ups["Taking off"]["early_breakout"] is True and ups["Taking off"]["early_multiple"] == 6.0
    assert ups["Normal"]["early_breakout"] is False and ups["Normal"]["early_multiple"] is None


def test_mark_seen_hides_old_uploads_and_returns_count(db):
    a = comp(db, 1)
    comp(db, 2, status="candidate")
    add_video(db, a, 1, 2, 10, title="Fresh")
    assert insights.mark_seen(db, now=NOW) == 1
    assert insights.new_uploads(db, now=NOW + timedelta(hours=1)) == []


def test_insights_endpoint_shape(client, db):
    seed_channel(db, comp(db, 1))
    body = client.get(f"{API}/insights").json()
    assert set(body) == {"competitors", "watchlist", "compare", "own_channel"}
    assert set(body["watchlist"]) == {"top_tags", "top_hashtags", "title_patterns", "format_split"}
    assert client.get(f"{API}/new-uploads").status_code == 200
    assert client.post(f"{API}/mark-seen").json() == {"marked": 1}
