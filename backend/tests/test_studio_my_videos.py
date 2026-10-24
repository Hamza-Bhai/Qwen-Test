"""Own video list of a connected channel, read with that channel's token."""

import pytest

from app.studio.models import StudioMyVideo
from app.studio.services.my_videos import sync_my_videos
from tests.studio_fakes import FakeSession, make_connection, open_db

CH = "https://www.googleapis.com/youtube/v3/channels"
PL = "https://www.googleapis.com/youtube/v3/playlistItems"
VID = "https://www.googleapis.com/youtube/v3/videos"


@pytest.fixture()
def db():
    with open_db() as session:
        yield session


def _video(vid, seconds, live="none"):
    return {
        "id": vid,
        "snippet": {"title": f"T {vid}", "publishedAt": "2026-09-01T10:00:00Z", "liveBroadcastContent": live},
        "contentDetails": {"duration": f"PT{seconds}S"},
    }


def _session(ids, *, uploads="UUmine", pages=None):
    pages = pages or [ids]
    routes = [(CH, None, {"items": [{"id": "UCmine1", "snippet": {"title": "AXIOM", "thumbnails": {"default": {"url": "t"}}},
                                     "contentDetails": {"relatedPlaylists": {"uploads": uploads} if uploads else {}}}]})]
    for i, page in enumerate(pages):
        token = {"pageToken": f"p{i}"} if i else None
        body = {"items": [{"contentDetails": {"videoId": v}} for v in page]}
        if i + 1 < len(pages):
            body["nextPageToken"] = f"p{i + 1}"
        routes.insert(1, (PL, token, body))  # most specific (pageToken) routes first
    videos = {"long1": _video("long1", 600), "short1": _video("short1", 40), "live1": _video("live1", 0, "upcoming")}
    videos.update({v: _video(v, 300) for p in pages for v in p if v not in videos})
    routes.append((VID, None, lambda params: {"items": [videos[v] for v in params["id"].split(",")]}))
    return FakeSession(routes)


def test_sync_my_videos_upserts_and_flags_shorts(db, monkeypatch):
    monkeypatch.setattr("app.services.channel_service.check_is_short", lambda vid, *a, **k: vid == "short1")
    conn = make_connection(db)
    for _ in range(2):
        assert sync_my_videos(db, conn, _session(["long1", "short1", "live1"])) == 3
    rows = {v.youtube_video_id: v for v in db.query(StudioMyVideo).all()}
    assert len(rows) == 3
    assert rows["long1"].is_short is False and rows["long1"].duration_seconds == 600
    assert rows["short1"].is_short is True
    assert rows["live1"].is_short is None and rows["live1"].live_broadcast_content == "upcoming"
    assert rows["long1"].published_at.year == 2026 and rows["long1"].title == "T long1"
    assert conn.title == "AXIOM" and conn.thumbnail_url == "t"


def test_sync_stops_at_max_videos(db):
    conn = make_connection(db)
    pages = [[f"v{i}" for i in range(50)], [f"w{i}" for i in range(50)]]
    session = _session(None, pages=pages)
    assert sync_my_videos(db, conn, session, max_videos=60) == 60
    assert db.query(StudioMyVideo).count() == 60
    assert all(len(p["id"].split(",")) <= 50 for m, u, p in session.calls if u == VID)


def test_channel_without_uploads_returns_zero(db):
    conn = make_connection(db)
    assert sync_my_videos(db, conn, _session([], uploads=None)) == 0
