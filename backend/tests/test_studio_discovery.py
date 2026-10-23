"""SerpApi-assisted competitor discovery (SerpApi and Data API both faked)."""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.models import Channel, Video
from app.db.session import SessionLocal, init_db
from app.services.channel_service import get_service
from app.studio.models import StudioCompetitor
from app.studio.services import discovery as d
from app.studio.services.settings_store import set_keywords
from app.studio.services.serpapi import SerpApiClient, SerpApiNotConfigured
from tests import conftest as cf

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)
UC = re.compile(r"^UC[A-Za-z0-9_-]{22}$")


def uc(n: int) -> str:
    return f"UC{n:022d}"


# ----------------------------------------------------------------- fixtures
@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SERPAPIKEY", "KEY-NEVER-LEAK")
    monkeypatch.delenv("SERPAPI_MONTHLY_CAP", raising=False)
    get_settings.cache_clear()


@pytest.fixture()
def db():
    init_db()
    with SessionLocal() as session:
        yield session


class RoutingHttp:
    """SerpApi HTTP stand-in: answers by engine (+ query / video id) and counts calls."""

    def __init__(self, search=None, related=None):
        self.search = search or {}    # search_query -> payload
        self.related = related or {}  # video id -> payload
        self.calls = []

    def get(self, url, params=None, timeout=None):
        p = dict(params or {})
        self.calls.append(p)
        if p["engine"] == "youtube":
            payload = self.search.get(p["search_query"], {"video_results": []})
        elif p["engine"] == "youtube_video":
            payload = self.related.get(p["v"], {"related_videos": []})
        else:
            payload = {}
        return type("R", (), {"status_code": 200, "json": lambda self_: payload})()


def search_payload(*channels):
    """channels: (name, channel_id-or-None, link) triples as SerpApi's video_results[*].channel."""
    return {"video_results": [
        {"title": "v", "video_id": f"s{i:010d}", "channel": {"name": n, "channel_id": cid, "link": link}}
        for i, (n, cid, link) in enumerate(channels)
    ]}


class DataApi:
    """Fake YouTube Data API: channels, uploads playlists, videos."""

    def __init__(self):
        self.channels = {}    # id -> item
        self.handles = {}     # '@name' (lowercase) -> id
        self.playlists = {}   # uploads playlist id -> [video ids]
        self.videos = {}      # video id -> item

    def channel(self, n, subs=5000, hidden=False, handle=None):
        stats = {"viewCount": "1", "videoCount": "50", "hiddenSubscriberCount": hidden}
        if not hidden:
            stats["subscriberCount"] = str(subs)
        self.channels[uc(n)] = {
            "id": uc(n),
            "snippet": {"title": f"Chan {n}", "publishedAt": "2020-01-01T00:00:00Z", "thumbnails": {}},
            "statistics": stats,
            "contentDetails": {"relatedPlaylists": {"uploads": f"UU{n:022d}"}},
        }
        if handle:
            self.handles[handle.lower()] = uc(n)
        return uc(n)

    def uploads(self, n, views, *, days_ago=3, count=5):
        ids = []
        for i in range(count):
            vid = f"u{n:04d}{i:06d}"
            self.videos[vid] = {
                "id": vid,
                "snippet": {"title": "t", "channelId": uc(n), "channelTitle": f"Chan {n}",
                            "publishedAt": (NOW - timedelta(days=days_ago + i)).isoformat().replace("+00:00", "Z")},
                "statistics": {"viewCount": str(views)},
                "contentDetails": {"duration": "PT9M"},
            }
            ids.append(vid)
        self.playlists[f"UU{n:022d}"] = ids

    def related_video(self, vid, channel_n):
        self.videos[vid] = {"id": vid, "snippet": {"channelId": uc(channel_n), "channelTitle": f"Chan {channel_n}"}}

    def install(self, monkeypatch):
        cf._invalidate_youtube_cache()
        api = self

        def fake_get(self, url, params=None, **kwargs):
            if not cf._is_google_api(url):
                return cf._ORIGINAL_HTTPX_GET(self, url, params=params, **kwargs)
            p = dict(params or {})
            path = urlparse(str(url)).path
            if "/channels" in path:
                if p.get("id"):
                    items = [api.channels[i] for i in p["id"].split(",") if i in api.channels]
                else:
                    cid = api.handles.get(str(p.get("forHandle", "")).lower())
                    items = [api.channels[cid]] if cid else []
                return cf.FakeResponse(200, {"items": items})
            if "/playlistItems" in path:
                ids = api.playlists.get(p["playlistId"], [])[: int(p.get("maxResults", 50))]
                return cf.FakeResponse(200, {"items": [
                    {"contentDetails": {"videoId": v, "videoPublishedAt": api.videos[v]["snippet"]["publishedAt"]}}
                    for v in ids]})
            if "/videos" in path:
                return cf.FakeResponse(200, {"items": [api.videos[i] for i in p["id"].split(",") if i in api.videos]})
            return cf.FakeResponse(404, {"error": {"message": "unexpected"}})

        monkeypatch.setattr(httpx.Client, "get", fake_get)


def make_owner(db, subs=1000, hidden=False):
    ch = Channel(youtube_channel_id=uc(900), title="Mine", subscriber_count=None if hidden else subs)
    db.add(ch)
    db.commit()
    for i, views in enumerate([900, 500, 300, 100]):
        db.add(Video(channel_id=ch.id, youtube_video_id=f"own{i:08d}", title="mine", view_count=views,
                     published_at=NOW - timedelta(days=30), is_short=False))
    db.commit()
    return ch


def run(db, http, youtube=None, **kw):
    serp = SerpApiClient(db, http=http)
    return d.discover(db, serp, youtube or get_service(db), now=NOW, **kw)


# --------------------------------------------------------- pure extraction
def test_candidates_from_search_fixture():
    payload = json.loads((FIXTURES / "serpapi_youtube_search.json").read_text(encoding="utf-8"))
    found = d.candidates_from_search(payload, "sunday reset")
    assert found, "the real fixture has channels"
    for c in found:
        assert UC.match(c.identifier) or (c.identifier.startswith("@") and c.identifier == c.identifier.lower())
        assert c.seeds == ("sunday reset",) and c.sources == ("search",) and c.appearances == 1


def test_candidates_from_search_prefers_channel_id_and_falls_back_to_handle_link():
    payload = search_payload(("A", uc(1), "https://www.youtube.com/@Alpha"),
                             ("B", None, "https://www.youtube.com/@Beta"),
                             ("C", None, "https://www.youtube.com/c/custom"),   # unsupported -> skipped
                             ("D", None, None))
    got = [c.identifier for c in d.candidates_from_search(payload, "k")]
    assert got == [uc(1), "@beta"]


def test_related_video_ids_and_items_from_fixture():
    payload = json.loads((FIXTURES / "serpapi_youtube_video.json").read_text(encoding="utf-8"))
    ids = d.related_video_ids(payload)
    assert ids and all(len(i) == 11 for i in ids)
    items = [{"id": ids[0], "snippet": {"channelId": uc(7), "channelTitle": "Seven"}},
             {"id": "x", "snippet": {}}]  # no channelId -> skipped
    got = d.candidates_from_video_items(items, "own top video")
    assert [(c.identifier, c.title, c.sources) for c in got] == [(uc(7), "Seven", ("related",))]


def test_merge_dedupes_handle_case_and_sums_appearances():
    a = d.Candidate("@alpha", "A", ("k1",), ("search",), 1)
    b = d.Candidate("@alpha", "A", ("k2",), ("search",), 1)
    c = d.Candidate(uc(1), "One", ("k1",), ("related",), 2)
    merged = {m.identifier: m for m in d.merge_candidates([[a], [b, c]])}
    assert merged["@alpha"].appearances == 2 and merged["@alpha"].seeds == ("k1", "k2")
    assert merged[uc(1)].appearances == 2 and merged[uc(1)].sources == ("related",)


# ------------------------------------------------------------------ discover
def scenario(db, monkeypatch, **owner):
    make_owner(db, **owner)
    api = DataApi()
    api.install(monkeypatch)
    return api


def test_discover_excludes_own_existing_and_rejected(db, monkeypatch):
    api = scenario(db, monkeypatch)
    for n in (1, 2, 3):
        api.channel(n)
        api.uploads(n, 2000)
    api.channel(900)  # the owner's own channel also shows up in results
    db.add(StudioCompetitor(youtube_channel_id=uc(2), title="W", status="watching", source="manual"))
    db.add(StudioCompetitor(youtube_channel_id=uc(3), title="R", status="rejected", source="manual"))
    db.commit()
    set_keywords(db, ["calm"])
    http = RoutingHttp(search={"calm": search_payload(("1", uc(1), None), ("2", uc(2), None),
                                                      ("3", uc(3), None), ("me", uc(900), None))})
    result = run(db, http)
    assert result.added == 1
    ids = {c.youtube_channel_id: c.status for c in db.query(StudioCompetitor)}
    assert ids[uc(1)] == "candidate" and ids[uc(2)] == "watching" and ids[uc(3)] == "rejected"
    assert uc(900) not in ids


def test_discover_applies_size_range_and_recency(db, monkeypatch):
    api = scenario(db, monkeypatch, subs=1000)   # accepted range: 200 .. 20 000
    for n, subs, days in [(1, 5000, 3), (2, 100, 3), (3, 50_000, 3), (4, 3000, 60)]:
        api.channel(n, subs=subs)
        api.uploads(n, 1000, days_ago=days)
    set_keywords(db, ["calm"])
    http = RoutingHttp(search={"calm": search_payload(*[(str(n), uc(n), None) for n in (1, 2, 3, 4)])})
    run(db, http)
    kept = {c.youtube_channel_id for c in db.query(StudioCompetitor)}
    assert kept == {uc(1)}   # too small, too big and stale (last upload 60 days ago) are dropped


def test_discover_unknown_subscribers_kept_but_ranked_last(db, monkeypatch):
    api = scenario(db, monkeypatch)
    api.channel(1, subs=8000); api.uploads(1, 8000)         # 1.0 views per subscriber
    api.channel(2, subs=5000); api.uploads(2, 2500)         # 0.5
    api.channel(3, hidden=True); api.uploads(3, 99999)      # unknown subscribers -> score 0
    set_keywords(db, ["calm"])
    http = RoutingHttp(search={"calm": search_payload(("1", uc(1), None), ("2", uc(2), None), ("3", uc(3), None))})
    result = run(db, http)
    order = [db.get(StudioCompetitor, i).youtube_channel_id for i in result.competitor_ids]
    assert order == [uc(1), uc(2), uc(3)]
    assert db.get(StudioCompetitor, result.competitor_ids[2]).subscriber_count is None


def test_discover_owner_with_hidden_subscribers_skips_size_filter(db, monkeypatch):
    api = scenario(db, monkeypatch, hidden=True)
    api.channel(1, subs=5_000_000); api.uploads(1, 1000)    # would be far out of range for a known owner
    set_keywords(db, ["calm"])
    run(db, RoutingHttp(search={"calm": search_payload(("1", uc(1), None))}))
    assert db.query(StudioCompetitor).count() == 1


def test_discover_stores_reason_and_source_and_never_watching(db, monkeypatch):
    api = scenario(db, monkeypatch)
    api.channel(1, subs=5000); api.uploads(1, 2500)
    set_keywords(db, ["calm"])
    http = RoutingHttp(search={"calm": search_payload(("1", uc(1), None)), })
    run(db, http)
    row = db.query(StudioCompetitor).one()
    assert row.status == "candidate" and row.source == "search"
    reason = row.discovery_reason
    assert reason["seeds"] == ["calm"] and reason["appearances"] >= 1
    assert reason["recent_median_views"] == 2500 and reason["views_per_subscriber"] == 0.5
    assert reason["days_since_last_upload"] == 3 and "calm" in reason["text"]


def test_discover_related_channels_are_resolved_through_videos_list(db, monkeypatch):
    api = scenario(db, monkeypatch)
    api.channel(7, subs=4000); api.uploads(7, 2000)
    api.related_video("rel00000001", 7)
    set_keywords(db, ["calm"])
    http = RoutingHttp(
        search={"calm": search_payload()},
        related={"own00000000": {"related_videos": [{"video_id": "rel00000001", "channel": {"name": "Seven"}}]}},
    )
    result = run(db, http)
    assert result.added == 1
    row = db.query(StudioCompetitor).one()
    assert row.youtube_channel_id == uc(7) and row.source == "related"
    # the owner's top video (most views) was the one looked up
    assert any(c.get("engine") == "youtube_video" and c.get("v") == "own00000000" for c in http.calls)


def test_discover_never_exceeds_max_searches(db, monkeypatch):
    scenario(db, monkeypatch)
    set_keywords(db, [f"kw{i}" for i in range(10)])
    http = RoutingHttp()
    result = run(db, http)
    assert len(http.calls) <= 8 and result.searches_used <= 8
    assert sum(1 for c in http.calls if c["engine"] == "youtube") == 5
    assert sum(1 for c in http.calls if c["engine"] == "youtube_video") == 3


def test_discover_budget_exhausted_returns_partial_with_reason(db, monkeypatch):
    api = scenario(db, monkeypatch)
    api.channel(1, subs=5000); api.uploads(1, 2500)
    monkeypatch.setenv("SERPAPI_MONTHLY_CAP", "1")
    get_settings.cache_clear()
    set_keywords(db, ["first", "second"])
    http = RoutingHttp(search={"first": search_payload(("1", uc(1), None))})
    result = run(db, http)
    assert result.stopped_reason == "budget" and result.searches_used == 1 and result.added == 1


def test_discover_reports_a_serpapi_failure_instead_of_returning_nothing_silently(db, monkeypatch):
    scenario(db, monkeypatch)
    set_keywords(db, ["calm", "focus"])

    class Down:
        calls = 0

        def get(self, url, params=None, timeout=None):
            Down.calls += 1
            raise httpx.ConnectError("dns failure")

    result = run(db, Down())
    assert result.stopped_reason == "serpapi_error" and "Cannot reach SerpApi" in result.message
    assert result.added == 0 and Down.calls == 1  # it stopped instead of hammering the other searches


def test_discover_skips_a_query_with_no_results_and_continues(db, monkeypatch):
    api = scenario(db, monkeypatch)
    api.channel(1, subs=5000)
    api.uploads(1, 2500)
    set_keywords(db, ["nothing here", "calm"])

    class Mixed(RoutingHttp):
        def get(self, url, params=None, timeout=None):
            if params.get("search_query") == "nothing here":
                self.calls.append(dict(params))
                return type("R", (), {"status_code": 200,
                                      "json": lambda s: {"error": "YouTube hasn't returned any results for this query."}})()
            return super().get(url, params=params, timeout=timeout)

    http = Mixed(search={"calm": search_payload(("1", uc(1), None))})
    result = run(db, http)
    assert result.stopped_reason is None and result.message is None and result.added == 1


def test_discover_same_channel_as_handle_and_channel_id_is_one_candidate(db, monkeypatch):
    api = scenario(db, monkeypatch)
    api.channel(1, subs=5000, handle="@alpha")
    api.uploads(1, 2500)
    set_keywords(db, ["calm", "focus"])
    http = RoutingHttp(search={
        "calm": search_payload(("One", uc(1), None)),                                  # found by channel id
        "focus": search_payload(("One", None, "https://www.youtube.com/@Alpha")),      # found by handle only
    })
    run(db, http)
    row = db.query(StudioCompetitor).one()
    assert row.youtube_channel_id == uc(1) and row.discovery_reason["appearances"] == 2
    assert set(row.discovery_reason["seeds"]) == {"calm", "focus"}


def test_discover_youtube_quota_exhausted_mid_run_returns_partial_with_reason(db, monkeypatch):
    from app.services.quota import QuotaExceededError

    api = scenario(db, monkeypatch)
    for n in (1, 2):
        api.channel(n, subs=5000)
        api.uploads(n, 2500)
    set_keywords(db, ["calm"])
    youtube = get_service(db)
    real = youtube.playlist_items
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] > 1:
            raise QuotaExceededError("YouTube daily quota exhausted")
        return real(*args, **kwargs)

    monkeypatch.setattr(youtube, "playlist_items", flaky)
    http = RoutingHttp(search={"calm": search_payload(("1", uc(1), None), ("2", uc(2), None))})
    result = run(db, http, youtube=youtube)
    assert result.stopped_reason == "youtube_quota" and result.added == 1


def test_discover_without_own_channel_raises(db):
    with pytest.raises(ValueError, match="Pick your channel"):
        run(db, RoutingHttp())


def test_discover_endpoint_409_when_serpapi_not_configured(db, monkeypatch):
    from app.main import app

    make_owner(db)
    monkeypatch.delenv("SERPAPIKEY", raising=False)
    get_settings.cache_clear()
    with TestClient(app) as client:
        assert client.post("/api/v1/studio/competitors/discover").status_code == 409
    with pytest.raises(SerpApiNotConfigured):
        run(db, RoutingHttp())
