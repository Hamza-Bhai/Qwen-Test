"""Hook study: first ~60 s of a competitor video's transcript via SerpApi."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.session import SessionLocal, get_db, init_db
from app.studio.models import StudioCompetitor, StudioCompetitorVideo
from app.studio.routes.deps import get_serpapi
from app.studio.services.hooks import NoTranscript, UnknownVideo, study_hook
from app.studio.services.serpapi import SerpApiClient
from tests.test_studio_serpapi import FakeHttp, FakeResponse

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)
VID = "hookvideo01"
FIXTURE = Path(__file__).parent / "fixtures" / "serpapi_youtube_transcript.json"


@pytest.fixture(autouse=True)
def _serpapi_env(monkeypatch):
    monkeypatch.setenv("SERPAPIKEY", "KEY-NEVER-LEAK")
    monkeypatch.delenv("SERPAPI_MONTHLY_CAP", raising=False)
    get_settings.cache_clear()


@pytest.fixture()
def db():
    init_db()
    with SessionLocal() as session:
        comp = StudioCompetitor(youtube_channel_id="UC" + "a" * 22, title="C", status="watching", source="manual")
        comp.videos.append(
            StudioCompetitorVideo(youtube_video_id=VID, title="t", first_seen_at=NOW, last_synced_at=NOW)
        )
        session.add(comp)
        session.commit()
        yield session


@pytest.fixture()
def api():
    from app.main import app

    init_db()
    with TestClient(app) as client:
        yield app, client
    app.dependency_overrides.clear()


def segments(*starts_ms):
    return {"transcript": [
        {"start_ms": s, "snippet": f"line at {s // 1000}s", "start_time_text": "x", "start_time_label": "y"}
        for s in starts_ms
    ]}


def test_hook_text_uses_only_first_60_seconds(db):
    http = FakeHttp(FakeResponse(200, segments(*range(0, 80_000, 10_000))))
    result = study_hook(db, SerpApiClient(db, http=http), VID)
    assert result["hook_text"] == " ".join(f"line at {s}s" for s in range(0, 60, 10))
    assert [s["start_ms"] for s in result["segments"]] == list(range(0, 80_000, 10_000))
    assert result["seconds_covered"] == 50
    url, params = http.calls[0]
    assert params["engine"] == "youtube_video_transcript" and params["v"] == VID


def test_first_segment_is_kept_even_if_transcript_starts_late(db):
    http = FakeHttp(FakeResponse(200, segments(65_000, 75_000)))
    result = study_hook(db, SerpApiClient(db, http=http), VID)
    assert result["hook_text"] == "line at 65s"


def test_hook_is_stored_on_the_video(db):
    study_hook(db, SerpApiClient(db, http=FakeHttp(FakeResponse(200, segments(0, 10_000)))), VID)
    row = db.query(StudioCompetitorVideo).filter_by(youtube_video_id=VID).one()
    assert row.hook_text == "line at 0s line at 10s"
    assert row.hook_segments[0] == {"start_ms": 0, "text": "line at 0s"}


def test_real_fixture_transcript_parses(db):
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    result = study_hook(db, SerpApiClient(db, http=FakeHttp(FakeResponse(200, payload))), VID)
    assert result["hook_text"].strip() != "" and len(result["segments"]) == len(payload["transcript"])


def test_second_call_uses_cache_and_costs_nothing(db):
    http = FakeHttp(FakeResponse(200, segments(0, 10_000)))
    client = SerpApiClient(db, http=http)
    study_hook(db, client, VID)
    study_hook(db, client, VID)
    assert len(http.calls) == 1 and client.usage().used == 1


def test_empty_transcript_raises_and_stores_nothing(db):
    with pytest.raises(NoTranscript, match="No transcript available"):
        study_hook(db, SerpApiClient(db, http=FakeHttp(FakeResponse(200, {"transcript": []}))), VID)
    assert db.query(StudioCompetitorVideo).filter_by(youtube_video_id=VID).one().hook_text is None


def test_unknown_video_raises_before_any_search(db):
    http = FakeHttp()
    with pytest.raises(UnknownVideo):
        study_hook(db, SerpApiClient(db, http=http), "doesnotexist")
    assert http.calls == []


def _override(app, http):
    def dep(session=Depends(get_db)):
        return SerpApiClient(session, http=http)

    app.dependency_overrides[get_serpapi] = dep


def test_api_post_then_get_roundtrip(api, db):
    app, client = api
    _override(app, FakeHttp(FakeResponse(200, segments(0, 10_000))))
    url = f"/api/v1/studio/competitors/videos/{VID}/hook"
    assert client.get(url).status_code == 404  # nothing stored yet
    res = client.post(url)
    assert res.status_code == 200 and res.json()["hook_text"] == "line at 0s line at 10s"
    assert client.get(url).json()["hook_text"] == "line at 0s line at 10s"


def test_api_unknown_video_and_no_transcript_are_404(api, db):
    app, client = api
    _override(app, FakeHttp(FakeResponse(200, {"transcript": []})))
    base = "/api/v1/studio/competitors/videos"
    assert client.post(f"{base}/doesnotexist/hook").status_code == 404
    assert client.post(f"{base}/{VID}/hook").status_code == 404


def test_budget_exceeded_maps_to_429(api, db, monkeypatch):
    app, client = api
    monkeypatch.setenv("SERPAPI_MONTHLY_CAP", "0")
    get_settings.cache_clear()
    _override(app, FakeHttp())
    assert client.post(f"/api/v1/studio/competitors/videos/{VID}/hook").status_code == 429


def test_not_configured_maps_to_409(api, db, monkeypatch):
    app, client = api
    monkeypatch.delenv("SERPAPIKEY", raising=False)
    get_settings.cache_clear()
    _override(app, FakeHttp())
    assert client.post(f"/api/v1/studio/competitors/videos/{VID}/hook").status_code == 409
