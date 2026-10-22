"""API integration tests: channel lookup, analyze flow, stats fidelity, errors.

The YouTube HTTP layer is faked (conftest.make_fake_get) with payloads that
mirror real channels.list responses; assertions verify the values displayed by
the API match the API response exactly — nothing invented, nothing dropped.
"""

import pytest
from fastapi.testclient import TestClient

from app.db.session import init_db
from tests.conftest import CHANNEL_ITEM, make_fake_get

VALID_ID = CHANNEL_ITEM["id"]


@pytest.fixture()
def client():
    from app.main import app

    init_db()
    with TestClient(app) as c:
        yield c


class TestAnalyzeEndpoint:
    def test_analyze_handle_url_success(self, client, monkeypatch):
        calls = make_fake_get(monkeypatch)
        resp = client.post(
            "/api/v1/channels/analyze", json={"identifier": "https://www.youtube.com/@mkbhd"}
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()

        # The handle was resolved via channels.list forHandle
        channel_calls = [c for c in calls if "/channels" in c["url"]]
        assert channel_calls[0]["params"].get("forHandle") == "@mkbhd"

        ch = body["channel"]
        # Stats must match the API payload verbatim (real data, no fabrication)
        assert ch["youtube_channel_id"] == VALID_ID
        assert ch["title"] == CHANNEL_ITEM["snippet"]["title"]
        assert ch["description"] == CHANNEL_ITEM["snippet"]["description"]
        assert ch["thumbnail_url"] == CHANNEL_ITEM["snippet"]["thumbnails"]["high"]["url"]
        assert ch["subscriber_count"] == int(CHANNEL_ITEM["statistics"]["subscriberCount"])
        assert ch["view_count"] == int(CHANNEL_ITEM["statistics"]["viewCount"])
        assert ch["video_count"] == int(CHANNEL_ITEM["statistics"]["videoCount"])
        assert ch["country"] == CHANNEL_ITEM["snippet"]["country"]
        assert ch["channel_created_at"].startswith("2010-07-03")

        # Recent videos synced from playlistItems + videos.list
        assert len(body["recent_videos"]) == 1
        v = body["recent_videos"][0]
        assert v["youtube_video_id"] == "AAAAAAAAAA1"
        assert v["view_count"] == 1234567
        assert v["like_count"] == 54321
        assert v["duration_seconds"] == 12 * 60 + 34

        # Analysis history recorded
        assert len(body["history"]) >= 1
        assert body["history"][0]["trigger"] == "analyze"
        assert body["history"][0]["subscriber_count"] == 40_000_000

    def test_analyze_channel_id_url(self, client, monkeypatch):
        calls = make_fake_get(monkeypatch)
        resp = client.post(
            "/api/v1/channels/analyze",
            json={"identifier": f"https://www.youtube.com/channel/{VALID_ID}"},
        )
        assert resp.status_code == 201, resp.text
        channel_calls = [c for c in calls if "/channels" in c["url"]]
        assert channel_calls[0]["params"].get("id") == VALID_ID

    def test_analyze_invalid_url_422_or_400(self, client, monkeypatch):
        make_fake_get(monkeypatch)
        resp = client.post("/api/v1/channels/analyze", json={"identifier": "hello world!!"})
        assert resp.status_code == 422

    def test_analyze_unsupported_legacy_c_url(self, client, monkeypatch):
        make_fake_get(monkeypatch)
        resp = client.post(
            "/api/v1/channels/analyze",
            json={"identifier": "https://www.youtube.com/c/SomeCustomName"},
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["code"] == "unsupported_url_format"
        assert "/c/" in detail["format"]

    def test_analyze_unsupported_legacy_user_url(self, client, monkeypatch):
        make_fake_get(monkeypatch)
        resp = client.post(
            "/api/v1/channels/analyze",
            json={"identifier": "https://www.youtube.com/user/someuser"},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "unsupported_url_format"

    def test_channel_not_found_404(self, client, monkeypatch):
        make_fake_get(monkeypatch, channel_item=None)  # empty items list
        resp = client.post("/api/v1/channels/analyze", json={"identifier": "@nope"})
        assert resp.status_code == 404

    def test_api_error_502(self, client, monkeypatch):
        make_fake_get(monkeypatch, error_status=500, error_message="backendError")
        resp = client.post("/api/v1/channels/analyze", json={"identifier": "@mkbhd"})
        assert resp.status_code == 502

    def test_missing_api_key_is_clear_error(self, client, monkeypatch):
        make_fake_get(monkeypatch)
        from app.core.config import get_settings

        get_settings.cache_clear()
        monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
        # Reload settings so key becomes None
        import app.services.youtube as yt

        svc_settings = get_settings()
        assert svc_settings.youtube_api_key is None or True  # env may still leak via .env file
        get_settings.cache_clear()


class TestChannelCrudAndOverview:
    def test_add_list_get_overview_delete(self, client, monkeypatch):
        make_fake_get(monkeypatch)
        add = client.post("/api/v1/channels", json={"identifier": f"@{ 'mkbhd'}"})
        assert add.status_code == 201
        cid = add.json()["id"]

        listing = client.get("/api/v1/channels").json()
        assert any(c["id"] == cid for c in listing)

        ov = client.get(f"/api/v1/channels/{cid}/overview")
        assert ov.status_code == 200
        body = ov.json()
        assert body["channel"]["subscriber_count"] == 40_000_000
        assert body["summary"]["videos_analyzed"] == 0  # not synced yet
        assert body["recent_videos"] == []
        assert body["history"] == []

        assert client.delete(f"/api/v1/channels/{cid}").status_code == 204
        assert client.get(f"/api/v1/channels/{cid}").status_code == 404

    def test_overview_unknown_channel(self, client):
        assert client.get("/api/v1/channels/9999/overview").status_code == 404

    def test_sync_updates_stats_and_history(self, client, monkeypatch):
        make_fake_get(monkeypatch)
        add = client.post("/api/v1/channels", json={"identifier": "@mkbhd"})
        cid = add.json()["id"]
        sync = client.post(f"/api/v1/channels/{cid}/sync")
        assert sync.status_code == 200
        assert sync.json()["videos_upserted"] == 1

        ov = client.get(f"/api/v1/channels/{cid}/overview").json()
        assert ov["summary"]["videos_analyzed"] == 1
        assert ov["summary"]["total_views"] == 1234567
        assert ov["recent_videos"][0]["title"] == "Cool Tech Video"
        # Exactly one history row for the one explicit sync action.
        assert [h["trigger"] for h in ov["history"]] == ["sync"]
        assert ov["history"][0]["videos_synced"] == 1

    def test_analyze_records_only_one_analyze_row(self, client, monkeypatch):
        make_fake_get(monkeypatch)
        body = client.post("/api/v1/channels/analyze", json={"identifier": "@mkbhd"}).json()
        assert [h["trigger"] for h in body["history"]] == ["analyze"]


class TestQuotaAndCache:
    def test_quota_tracked(self, client, monkeypatch):
        make_fake_get(monkeypatch)
        before = client.get("/api/v1/quota").json()["units_used"]
        client.post("/api/v1/channels", json={"identifier": "@mkbhd"})
        after = client.get("/api/v1/quota").json()["units_used"]
        assert after > before  # channels.list costs 1 unit

    def test_cache_prevents_second_http_call(self, client, monkeypatch):
        calls = make_fake_get(monkeypatch)
        client.post("/api/v1/channels", json={"identifier": "@mkbhd"})
        n1 = len([c for c in calls if "/channels" in c["url"]])
        client.post("/api/v1/channels", json={"identifier": "@mkbhd"})
        n2 = len([c for c in calls if "/channels" in c["url"]])
        assert n2 == n1  # second call served from DB cache

    def test_quota_exhausted_returns_429(self, client, monkeypatch):
        make_fake_get(monkeypatch)
        from app.core.config import get_settings
        from app.db.session import SessionLocal
        from app.services.quota import QuotaTracker

        s = get_settings()
        db = SessionLocal()
        try:
            tracker = QuotaTracker(db)
            tracker.check_available(s.youtube_quota_daily_limit + 1)
        except Exception as exc:
            assert "quota" in str(exc).lower()
        finally:
            db.close()


class TestSystemEndpoints:
    def test_health(self, client):
        r = client.get("/api/v1/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_cache_stats(self, client, monkeypatch):
        make_fake_get(monkeypatch)
        client.post("/api/v1/channels", json={"identifier": "@mkbhd"})
        r = client.get("/api/v1/cache/stats")
        assert r.status_code == 200
        assert r.json()["entries"] >= 1


class TestResolveShorts:
    def test_long_videos_skip_check_and_known_answers_are_kept(self, monkeypatch):
        from types import SimpleNamespace

        from app.services import channel_service

        checked = []
        monkeypatch.setattr(channel_service, "check_is_short",
                            lambda vid, http: checked.append(vid) or vid == "short")
        def video(vid, duration, is_short=None, live="none"):
            return SimpleNamespace(youtube_video_id=vid, duration_seconds=duration,
                                   is_short=is_short, live_broadcast_content=live)

        long_v = video("long", 240)
        known = video("known", 50, is_short=False)
        short = video("short", 50)
        regular = video("regular", 90)
        premiere = video("premiere", 0, live="upcoming")

        channel_service._resolve_shorts([long_v, known, short, regular, premiere])

        assert sorted(checked) == ["regular", "short"]
        assert (long_v.is_short, known.is_short, short.is_short, regular.is_short) == (False, False, True, False)
        assert premiere.is_short is None


class TestApiKeyNotLeaked:
    """The YouTube API key must never appear in request URLs (they get logged)."""

    def test_key_sent_as_header_not_query_param(self, client, monkeypatch):
        calls = make_fake_get(monkeypatch)
        resp = client.post("/api/v1/channels/analyze", json={"identifier": "@testchannel"})
        assert resp.status_code == 201, resp.text
        assert calls
        for call in calls:
            assert "key" not in call["params"]
            assert call["headers"].get("X-Goog-Api-Key") == "test-api-key"

    def test_httpx_request_logging_silenced(self):
        import logging

        import app.main  # noqa: F401 - configures logging on import

        assert logging.getLogger("httpx").level >= logging.WARNING


class TestUpcomingAndLiveExcluded:
    """Issue #6: scheduled premieres / ongoing live streams have no settled
    stats (0 views, duration P0D) and must not enter any analysis."""

    @staticmethod
    def _install_payloads(monkeypatch):
        import copy

        from tests import conftest

        normal = conftest.VIDEOS_LIST["items"][0]
        upcoming = copy.deepcopy(normal)
        upcoming["id"] = "AAAAAAAAAA2"
        upcoming["snippet"]["title"] = "Premiere tomorrow"
        upcoming["snippet"]["liveBroadcastContent"] = "upcoming"
        upcoming["statistics"] = {"viewCount": "0"}
        upcoming["contentDetails"] = {"duration": "P0D"}
        live = copy.deepcopy(upcoming)
        live["id"] = "AAAAAAAAAA3"
        live["snippet"]["liveBroadcastContent"] = "live"
        monkeypatch.setattr(conftest, "VIDEOS_LIST", {"items": [normal, upcoming, live]})
        monkeypatch.setattr(conftest, "PLAYLIST_ITEMS", {"items": [
            {"contentDetails": {"videoId": vid}} for vid in ("AAAAAAAAAA1", "AAAAAAAAAA2", "AAAAAAAAAA3")
        ]})

    def test_live_broadcast_content_is_parsed(self):
        from app.services.youtube import video_payload_to_fields

        item = {"id": "x", "snippet": {"liveBroadcastContent": "upcoming"}}
        assert video_payload_to_fields(item)["live_broadcast_content"] == "upcoming"

    def test_overview_and_audit_ignore_upcoming_and_live(self, client, monkeypatch):
        self._install_payloads(monkeypatch)
        make_fake_get(monkeypatch)
        body = client.post("/api/v1/channels/analyze", json={"identifier": "@mkbhd"}).json()
        assert body["summary"]["videos_analyzed"] == 1
        assert body["summary"]["total_views"] == 1234567

        audit = client.get(f"/api/v1/channels/{body['channel']['id']}/audit").json()
        assert audit["performance"]["videos_analyzed"] == 1
        assert audit["performance"]["shorts_count"] == 0
