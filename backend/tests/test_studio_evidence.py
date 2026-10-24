"""Evidence pack: ids, competitor breakouts, topic gaps, own top/low videos, market signals, hash."""

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.studio.evidence import build_evidence, evidence_hash
from app.studio.models import StudioIdea, StudioMyVideo, StudioVideoTotals
from tests.studio_fakes import make_connection, open_db
from tests.test_studio_insights import add_video, comp, seed_channel

TODAY = date(2026, 10, 1)
NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)
FIX = Path(__file__).parent / "fixtures"


@pytest.fixture()
def db():
    with open_db() as session:
        yield session


class FakeSerp:
    def __init__(self):
        self.calls = 0

    def get(self, engine, params, **kw):
        self.calls += 1
        name = "serpapi_google_trends.json" if engine == "google_trends" else "serpapi_autocomplete.json"
        return json.loads((FIX / name).read_text(encoding="utf-8"))


def own_video(db, conn, vid, *, days_old, views, short=False, title=None):
    db.add(StudioMyVideo(connection_id=conn.id, youtube_video_id=vid, title=title or f"Own {vid}", is_short=short,
                         live_broadcast_content="none", published_at=NOW - timedelta(days=days_old)))
    db.add(StudioVideoTotals(connection_id=conn.id, youtube_video_id=vid, as_of=TODAY, views=views, watch_minutes=0,
                             avg_view_duration_s=0, avg_view_pct=50.0, subs_gained=0))
    db.commit()


def ids(pack, prefix):
    return [i["id"] for i in pack["items"] if i["id"].startswith(prefix)]


def test_pack_ids_unique_and_prefixed(db):
    conn = make_connection(db)
    own_video(db, conn, "o1", days_old=20, views=200)
    pack = build_evidence(db, conn, serpapi=FakeSerp(), today=TODAY)
    all_ids = [i["id"] for i in pack["items"]]
    assert len(all_ids) == len(set(all_ids)) and all_ids == sorted(all_ids)
    assert {i.split(":")[0] for i in all_ids} <= {"F", "P", "TOP", "LOW", "B", "GAP", "TR", "AC"}
    assert len(ids(pack, "F:")) == 9 and "P:views" in all_ids
    assert ids(pack, "TR:") and ids(pack, "AC:")
    assert all(i["text"] for i in pack["items"])
    assert pack["channel"]["title"] == conn.title and pack["own_titles"] == ["Own o1"]


def test_competitor_breakouts_and_gap_tokens_exclude_own_titles_and_hashtags(db):
    conn = make_connection(db)
    own_video(db, conn, "o1", days_old=20, views=200, title="Silent morning habits #calm")
    for n, outlier_title in ((1, "Quiet discipline at work #calm"), (2, "Quiet discipline for students #calm")):
        row = comp(db, n)
        seed_channel(db, row, outlier=False)
        add_video(db, row, 99, 10, 4000, title=outlier_title)
    pack = build_evidence(db, conn, serpapi=None, today=TODAY)
    breakouts = [i for i in pack["items"] if i["id"].startswith("B:")]
    assert len(breakouts) == 2 and breakouts[0]["data"]["multiple"] == pytest.approx(4.0)
    assert breakouts[0]["data"]["url"].startswith("https://www.youtube.com/watch?v=")
    gaps = ids(pack, "GAP:")
    assert "GAP:quiet" in gaps and "GAP:discipline" in gaps
    assert "GAP:calm" not in gaps  # hashtag, and own titles use it


def test_top_low_per_format(db):
    conn = make_connection(db)
    for i in range(6):
        own_video(db, conn, f"s{i}", days_old=10, views=100 * (i + 1), short=True)
    for i in range(6):
        own_video(db, conn, f"l{i}", days_old=10, views=10 * (i + 1))
    own_video(db, conn, "new", days_old=3, views=99999, short=True)  # too young to judge
    pack = build_evidence(db, conn, serpapi=None, today=TODAY)
    top = {i["id"]: i["data"] for i in pack["items"] if i["id"].startswith("TOP:")}
    low = {i["id"]: i["data"] for i in pack["items"] if i["id"].startswith("LOW:")}
    assert "TOP:s5" in top and "TOP:l5" in top and "TOP:new" not in top
    assert "LOW:s0" in low and "LOW:l0" in low
    assert top["TOP:s5"]["format"] == "short" and top["TOP:s5"]["views_per_day"] == pytest.approx(60.0)
    assert len([k for k in top if k.startswith("TOP:s")]) == 5
    assert pack["channel"]["format_mix"] == {"short": 7, "long": 6}


def test_pack_without_competitors_or_serpapi_has_notes(db):
    conn = make_connection(db)
    pack = build_evidence(db, conn, serpapi=None, today=TODAY)
    assert ids(pack, "B:") == [] and ids(pack, "TR:") == [] and ids(pack, "AC:") == []
    assert any("competitor" in n.lower() for n in pack["notes"])
    assert any("serpapi" in n.lower() for n in pack["notes"])


def test_earlier_ideas_listed(db):
    conn = make_connection(db)
    db.add(StudioIdea(connection_id=conn.id, status="new", format="short", topic="Sunday reset",
                      payload={"titles": ["Reset your Sunday in 10 minutes"]}))
    db.commit()
    pack = build_evidence(db, conn, serpapi=None, today=TODAY)
    assert pack["earlier_ideas"] == ["Sunday reset — Reset your Sunday in 10 minutes"]


def test_hash_stable_and_changes_with_items(db):
    conn = make_connection(db)
    own_video(db, conn, "o1", days_old=20, views=200)
    first = build_evidence(db, conn, serpapi=None, today=TODAY)
    again = build_evidence(db, conn, serpapi=None, today=TODAY)
    assert evidence_hash(first) == evidence_hash(again) and len(evidence_hash(first)) == 64
    own_video(db, conn, "o2", days_old=20, views=900)
    assert evidence_hash(build_evidence(db, conn, serpapi=None, today=TODAY)) != evidence_hash(first)
