"""Competitor sync: stores videos/snapshots from the (faked) Data API."""

from urllib.parse import urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app.db.session import SessionLocal, init_db
from app.studio.models import StudioCompetitor, StudioCompetitorVideo, StudioCompetitorVideoSnapshot
from app.studio.services import competitor_sync
from tests import conftest as cf

API = "/api/v1/studio/competitors"


@pytest.fixture()
def client():
    from app.main import app

    init_db()
    with TestClient(app) as c:
        yield c


def channel_item(n: int, subs="1000", hidden=False):
    stats = {"viewCount": "5000", "videoCount": "30", "hiddenSubscriberCount": hidden}
    if not hidden:
        stats["subscriberCount"] = subs
    return {
        "id": f"UC{n:022d}",
        "snippet": {"title": f"Comp {n}", "publishedAt": "2020-01-01T00:00:00Z", "thumbnails": {}},
        "statistics": stats,
        "contentDetails": {"relatedPlaylists": {"uploads": f"UU{n:022d}"}},
    }


def video_item(vid, title="A video", published="2026-09-01T00:00:00Z", views="100", duration="PT10M",
               tags=None, desc=""):
    return {
        "id": vid,
        "snippet": {"title": title, "description": desc, "publishedAt": published, "tags": tags,
                    "liveBroadcastContent": "none", "thumbnails": {}},
        "statistics": {"viewCount": views, "likeCount": "5", "commentCount": "1"},
        "contentDetails": {"duration": duration},
    }


def fake_youtube(monkeypatch, channels: dict, videos: dict):
    """channels: {channel_id: item}; videos: {uploads_playlist_id: [video items]}."""
    cf._invalidate_youtube_cache()

    def fake_get(self, url, params=None, **kwargs):
        if not cf._is_google_api(url):
            return cf._ORIGINAL_HTTPX_GET(self, url, params=params, **kwargs)
        p = dict(params or {})
        path = urlparse(str(url)).path
        if "/channels" in path:
            item = channels.get(p.get("id"))
            if item is None and p.get("forHandle"):
                item = next(iter(channels.values()), None)
            return cf.FakeResponse(200, {"items": [item] if item else []})
        if "/playlistItems" in path:
            items = videos.get(p["playlistId"], [])
            return cf.FakeResponse(200, {"items": [
                {"contentDetails": {"videoId": v["id"], "videoPublishedAt": v["snippet"]["publishedAt"]}}
                for v in items]})
        if "/videos" in path:
            wanted = p["id"].split(",")
            found = [v for vs in videos.values() for v in vs if v["id"] in wanted]
            return cf.FakeResponse(200, {"items": found})
        return cf.FakeResponse(404, {"error": {"message": "unexpected"}})

    monkeypatch.setattr(httpx.Client, "get", fake_get)


def add(db, n, status="watching"):
    comp = StudioCompetitor(
        youtube_channel_id=f"UC{n:022d}", title=f"Comp {n}", status=status, source="manual",
        uploads_playlist_id=f"UU{n:022d}",
    )
    db.add(comp)
    db.commit()
    db.refresh(comp)
    return comp


def vid(n, i):
    return f"v{n}{i:09d}"[:11]


def test_sync_stores_videos_with_hashtags_and_tags(monkeypatch):
    init_db()
    fake_youtube(monkeypatch, {f"UC{1:022d}": channel_item(1)}, {f"UU{1:022d}": [
        video_item(vid(1, 1), title="Sunday reset", tags=["calm"], desc="morning #Reset and #focus #Reset"),
        video_item(vid(1, 2), duration="PT45S"),
    ]})
    with SessionLocal() as db:
        comp = add(db, 1)
        result = competitor_sync.sync_competitor(db, comp)
        assert result["videos_upserted"] == 2 and result["quota_units_used"] >= 1
        row = db.query(StudioCompetitorVideo).filter_by(youtube_video_id=vid(1, 1)).one()
        assert row.competitor_id == comp.id
        assert row.tags == ["calm"] and row.hashtags == ["reset", "focus"]
        assert row.view_count == 100 and row.is_short is False  # 10 minutes is never a Short
        assert row.published_at is not None and row.duration_seconds == 600


def test_sync_records_one_snapshot_per_video_per_sync(monkeypatch):
    init_db()
    fake_youtube(monkeypatch, {f"UC{1:022d}": channel_item(1)}, {f"UU{1:022d}": [video_item(vid(1, 1))]})
    with SessionLocal() as db:
        comp = add(db, 1)
        competitor_sync.sync_competitor(db, comp)
        competitor_sync.sync_competitor(db, comp)
        assert db.query(StudioCompetitorVideoSnapshot).count() == 2


def test_resync_updates_stats_without_duplicating_videos(monkeypatch):
    init_db()
    channels = {f"UC{1:022d}": channel_item(1)}
    fake_youtube(monkeypatch, channels, {f"UU{1:022d}": [video_item(vid(1, 1), views="100")]})
    with SessionLocal() as db:
        comp = add(db, 1)
        competitor_sync.sync_competitor(db, comp)
        fake_youtube(monkeypatch, channels, {f"UU{1:022d}": [video_item(vid(1, 1), views="900")]})
        competitor_sync.sync_competitor(db, comp)
        rows = db.query(StudioCompetitorVideo).all()
        assert len(rows) == 1 and rows[0].view_count == 900


def test_sync_sets_last_synced_and_channel_stats(monkeypatch):
    init_db()
    fake_youtube(monkeypatch, {f"UC{1:022d}": channel_item(1, subs="4321")}, {f"UU{1:022d}": []})
    with SessionLocal() as db:
        comp = add(db, 1)
        assert comp.last_synced_at is None
        competitor_sync.sync_competitor(db, comp)
        db.refresh(comp)
        assert comp.last_synced_at is not None and comp.subscriber_count == 4321 and comp.video_count == 30


def test_hidden_subscriber_count_is_stored_as_none(monkeypatch):
    init_db()
    fake_youtube(monkeypatch, {f"UC{1:022d}": channel_item(1, hidden=True)}, {f"UU{1:022d}": []})
    with SessionLocal() as db:
        comp = add(db, 1)
        comp.subscriber_count = 777
        competitor_sync.sync_competitor(db, comp)
        db.refresh(comp)
        assert comp.subscriber_count is None


def test_sync_all_skips_candidates_and_rejected(monkeypatch):
    init_db()
    channels = {f"UC{n:022d}": channel_item(n) for n in (1, 2, 3)}
    fake_youtube(monkeypatch, channels, {f"UU{n:022d}": [video_item(vid(n, 1))] for n in (1, 2, 3)})
    with SessionLocal() as db:
        add(db, 1, "watching")
        add(db, 2, "candidate")
        add(db, 3, "rejected")
        results = competitor_sync.sync_all_watching(db)
        assert [r["competitor"] for r in results] == [f"UC{1:022d}"]
        assert db.query(StudioCompetitorVideo).count() == 1


def test_one_failing_channel_does_not_stop_the_rest(monkeypatch):
    init_db()
    channels = {f"UC{2:022d}": channel_item(2)}  # channel 1 is unknown to the fake -> 404
    fake_youtube(monkeypatch, channels, {f"UU{2:022d}": [video_item(vid(2, 1))]})
    with SessionLocal() as db:
        add(db, 1)
        add(db, 2)
        results = {r["competitor"]: r for r in competitor_sync.sync_all_watching(db)}
        assert "error" in results[f"UC{1:022d}"]
        assert results[f"UC{2:022d}"]["videos_upserted"] == 1


def test_sync_endpoint_409_for_candidate_and_404_for_unknown(client, monkeypatch):
    fake_youtube(monkeypatch, {f"UC{1:022d}": channel_item(1)}, {f"UU{1:022d}": []})
    with SessionLocal() as db:
        cid = add(db, 1, "candidate").id
    assert client.post(f"{API}/{cid}/sync").status_code == 409
    assert client.post(f"{API}/9999/sync").status_code == 404


def test_sync_endpoints_return_results(client, monkeypatch):
    fake_youtube(monkeypatch, {f"UC{1:022d}": channel_item(1)}, {f"UU{1:022d}": [video_item(vid(1, 1))]})
    with SessionLocal() as db:
        cid = add(db, 1).id
    one = client.post(f"{API}/{cid}/sync")
    assert one.status_code == 200 and one.json()["videos_upserted"] == 1
    everything = client.post(f"{API}/sync").json()
    assert len(everything["results"]) == 1
