"""SerpApi client: budget guard, cache, error handling, key hygiene."""

import json
from datetime import datetime

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import ApiCache, QuotaUsage
from app.db.session import SessionLocal, init_db
from app.services.quota import QUOTA_TIMEZONE
from app.studio.services.serpapi import (
    SerpApiBudgetExceeded,
    SerpApiClient,
    SerpApiError,
    SerpApiNoResults,
    SerpApiNotConfigured,
)

KEY = "KEY-NEVER-LEAK"


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class FakeHttp:
    """Stands in for httpx.Client: records calls, replays queued responses."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if not self.responses:
            return FakeResponse(200, {"ok": True})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture()
def db():
    init_db()
    with SessionLocal() as session:
        yield session


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setenv("SERPAPIKEY", KEY)
    monkeypatch.delenv("SERPAPI_MONTHLY_CAP", raising=False)
    get_settings.cache_clear()


def _rows(db):
    return db.execute(select(ApiCache).where(ApiCache.service == "serpapi")).scalars().all()


def test_not_configured_raises_before_any_http(db, monkeypatch):
    monkeypatch.delenv("SERPAPIKEY", raising=False)
    get_settings.cache_clear()
    http = FakeHttp()
    with pytest.raises(SerpApiNotConfigured):
        SerpApiClient(db, http=http).get("youtube", {"search_query": "x"})
    assert http.calls == []


def test_success_returns_payload_records_one_unit_and_caches(db):
    http = FakeHttp(FakeResponse(200, {"video_results": [1, 2]}))
    client = SerpApiClient(db, http=http)

    first = client.get("youtube", {"search_query": "sunday reset"})
    assert first["video_results"] == [1, 2]
    assert client.usage().used == 1
    url, params = http.calls[0]
    assert url == "https://serpapi.com/search.json"
    assert params["engine"] == "youtube" and params["search_query"] == "sunday reset"
    assert params["api_key"] == KEY

    second = client.get("youtube", {"search_query": "sunday reset"})
    assert second == first
    assert len(http.calls) == 1
    assert client.usage().used == 1


def test_different_params_are_different_cache_entries(db):
    http = FakeHttp()
    client = SerpApiClient(db, http=http)
    client.get("youtube", {"search_query": "a"})
    client.get("youtube", {"search_query": "b"})
    client.get("google_trends", {"search_query": "a"})
    assert len(http.calls) == 3
    assert len(_rows(db)) == 3
    assert all(len(r.cache_key) <= 255 for r in _rows(db))


def test_http_200_with_error_body_is_not_charged_or_cached(db):
    http = FakeHttp(FakeResponse(200, {"error": "Google hasn't returned any results"}))
    client = SerpApiClient(db, http=http)
    with pytest.raises(SerpApiError, match="hasn't returned any results"):
        client.get("youtube", {"search_query": "zzz"})
    assert client.usage().used == 0
    assert _rows(db) == []
    client.get("youtube", {"search_query": "zzz"})  # same call goes to HTTP again
    assert len(http.calls) == 2


def test_no_results_error_is_its_own_subclass(db):
    quiet = FakeHttp(FakeResponse(200, {"error": "YouTube Video Transcript hasn't returned any results for this query."}))
    with pytest.raises(SerpApiNoResults):
        SerpApiClient(db, http=quiet).get("youtube_video_transcript", {"v": "x"})
    other = FakeHttp(FakeResponse(401, {"error": "Invalid API key"}))
    with pytest.raises(SerpApiError) as exc:
        SerpApiClient(db, http=other).get("youtube", {"search_query": "x"})
    assert not isinstance(exc.value, SerpApiNoResults)


def test_http_error_status_raises_and_is_not_charged(db):
    http = FakeHttp(FakeResponse(401, {"error": "Invalid API key"}))
    client = SerpApiClient(db, http=http)
    with pytest.raises(SerpApiError) as exc:
        client.get("youtube", {"search_query": "x"})
    assert "Invalid API key" in str(exc.value)
    assert KEY not in str(exc.value)
    assert client.usage().used == 0


def test_network_failure_raises_serpapierror_not_charged(db):
    http = FakeHttp(httpx.ConnectError("boom"))
    client = SerpApiClient(db, http=http)
    with pytest.raises(SerpApiError):
        client.get("youtube", {"search_query": "x"})
    assert client.usage().used == 0


def test_budget_exceeded_blocks_without_http(db, monkeypatch):
    monkeypatch.setenv("SERPAPI_MONTHLY_CAP", "2")
    get_settings.cache_clear()
    http = FakeHttp()
    client = SerpApiClient(db, http=http)
    client.get("youtube", {"search_query": "a"})
    client.get("youtube", {"search_query": "b"})
    with pytest.raises(SerpApiBudgetExceeded):
        client.get("youtube", {"search_query": "c"})
    assert len(http.calls) == 2
    # A cached repeat is free and still works at the cap.
    assert client.get("youtube", {"search_query": "a"}) == {"ok": True}
    usage = client.usage()
    assert (usage.used, usage.cap, usage.remaining) == (2, 2, 0)


def test_usage_counts_only_current_month(db):
    today = datetime.now(QUOTA_TIMEZONE).date()
    prev_month_day = (today.replace(day=1)).replace(
        year=today.year - 1 if today.month == 1 else today.year,
        month=12 if today.month == 1 else today.month - 1,
    )
    db.add(QuotaUsage(service="serpapi", day=prev_month_day, units_used=50))
    db.add(QuotaUsage(service="serpapi", day=today, units_used=3))
    db.add(QuotaUsage(service="youtube", day=today, units_used=999))
    db.commit()
    usage = SerpApiClient(db, http=FakeHttp()).usage()
    assert usage.used == 3
    assert usage.month == today.strftime("%Y-%m")
    assert usage.cap == 200 and usage.remaining == 197


def test_api_key_never_cached_or_returned(db):
    payload = {"search_parameters": {"engine": "youtube", "api_key": KEY}, "ok": True}
    client = SerpApiClient(db, http=FakeHttp(FakeResponse(200, payload)))
    result = client.get("youtube", {"search_query": "x"})
    assert "api_key" not in result.get("search_parameters", {})
    assert KEY not in json.dumps(result)
    assert all(KEY not in json.dumps(r.response_json) and KEY not in r.cache_key for r in _rows(db))
