"""Competitor watchlist: add / list / patch / remove (Data API faked)."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.session import SessionLocal, init_db
from app.studio.models import StudioCompetitor, StudioCompetitorVideo
from tests.conftest import CHANNEL_ITEM, EMPTY, make_fake_get

API = "/api/v1/studio/competitors"
CHANNEL_URL = f"https://www.youtube.com/channel/{CHANNEL_ITEM['id']}"


@pytest.fixture()
def client():
    from app.main import app

    init_db()
    with TestClient(app) as c:
        yield c


def _item(n: int, **stats):
    item = {**CHANNEL_ITEM, "id": f"UC{n:022d}"}
    item["snippet"] = {**CHANNEL_ITEM["snippet"], "title": f"Comp {n}"}
    item["statistics"] = {**CHANNEL_ITEM["statistics"], **stats}
    return item


def test_add_by_handle_is_watching_with_real_fields(client, monkeypatch):
    make_fake_get(monkeypatch)
    res = client.post(API, json={"identifier": "@mkbhd", "notes": "tech giant"})
    assert res.status_code == 201
    body = res.json()
    assert body["status"] == "watching" and body["source"] == "manual"
    assert body["title"] == "MKBHD" and body["subscriber_count"] == 40_000_000
    assert body["handle"] == "@mkbhd" and body["notes"] == "tech giant"
    assert body["videos_stored"] == 0


def test_add_by_channel_url(client, monkeypatch):
    make_fake_get(monkeypatch)
    res = client.post(API, json={"identifier": CHANNEL_URL})
    assert res.status_code == 201
    assert res.json()["youtube_channel_id"] == CHANNEL_ITEM["id"]


def test_adding_twice_keeps_one_row(client, monkeypatch):
    make_fake_get(monkeypatch)
    client.post(API, json={"identifier": "@mkbhd"})
    client.post(API, json={"identifier": "@mkbhd"})
    assert len(client.get(API).json()) == 1


def test_unsupported_url_is_422_with_the_resolver_message(client, monkeypatch):
    make_fake_get(monkeypatch)
    res = client.post(API, json={"identifier": "https://www.youtube.com/c/somename"})
    assert res.status_code == 422
    assert "/c/" in res.json()["detail"]


def test_unknown_channel_is_404(client, monkeypatch):
    make_fake_get(monkeypatch, channel_item=EMPTY)
    assert client.post(API, json={"identifier": "@nobodyhere"}).status_code == 404


def test_quota_exhausted_is_429(client, monkeypatch):
    make_fake_get(monkeypatch)
    monkeypatch.setenv("YOUTUBE_QUOTA_DAILY_LIMIT", "0")
    get_settings.cache_clear()
    res = client.post(API, json={"identifier": "@mkbhd"})
    assert res.status_code == 429
    assert "quota" in res.json()["detail"].lower()


def test_hidden_subscriber_count_is_null(client, monkeypatch):
    item = _item(1)
    item["statistics"] = {"viewCount": "10", "videoCount": "2", "hiddenSubscriberCount": True}
    make_fake_get(monkeypatch, channel_item=item)
    body = client.post(API, json={"identifier": "@hidden"}).json()
    assert body["subscriber_count"] is None


def test_patch_notes_and_status(client, monkeypatch):
    make_fake_get(monkeypatch, channel_item=_item(1))
    cid = client.post(API, json={"identifier": "@one"}).json()["id"]
    res = client.patch(f"{API}/{cid}", json={"notes": "watch the shorts", "status": "candidate"})
    assert res.status_code == 200
    assert res.json()["notes"] == "watch the shorts" and res.json()["status"] == "candidate"
    cleared = client.patch(f"{API}/{cid}", json={"notes": None}).json()
    assert cleared["notes"] is None and cleared["status"] == "candidate"


def test_patch_bad_status_422_and_unknown_id_404(client, monkeypatch):
    make_fake_get(monkeypatch, channel_item=_item(1))
    cid = client.post(API, json={"identifier": "@one"}).json()["id"]
    assert client.patch(f"{API}/{cid}", json={"status": "banana"}).status_code == 422
    assert client.patch(f"{API}/9999", json={"notes": "x"}).status_code == 404


def test_delete_marks_rejected_and_drops_videos(client, monkeypatch):
    make_fake_get(monkeypatch, channel_item=_item(1))
    cid = client.post(API, json={"identifier": "@one"}).json()["id"]
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    with SessionLocal() as db:
        comp = db.get(StudioCompetitor, cid)
        comp.videos.append(
            StudioCompetitorVideo(youtube_video_id="vvvvvvvvvvv", title="t", first_seen_at=now, last_synced_at=now)
        )
        db.commit()
    assert client.get(API).json()[0]["videos_stored"] == 1

    assert client.delete(f"{API}/{cid}").status_code == 204
    assert client.get(f"{API}?status=watching").json() == []
    rejected = client.get(f"{API}?status=rejected").json()
    assert len(rejected) == 1 and rejected[0]["videos_stored"] == 0
    with SessionLocal() as db:
        assert db.query(StudioCompetitorVideo).count() == 0


def test_delete_unknown_id_is_404(client):
    assert client.delete(f"{API}/9999").status_code == 404


def test_readding_a_removed_channel_makes_it_watching_again(client, monkeypatch):
    make_fake_get(monkeypatch, channel_item=_item(1))
    cid = client.post(API, json={"identifier": "@one"}).json()["id"]
    client.delete(f"{API}/{cid}")
    again = client.post(API, json={"identifier": "@one"}).json()
    assert again["id"] == cid and again["status"] == "watching"


def test_list_filters_by_status(client, monkeypatch):
    make_fake_get(monkeypatch, channel_item=_item(1))
    client.post(API, json={"identifier": "@one"})
    make_fake_get(monkeypatch, channel_item=_item(2))
    second = client.post(API, json={"identifier": "@two"}).json()["id"]
    client.patch(f"{API}/{second}", json={"status": "candidate"})
    assert [c["title"] for c in client.get(f"{API}?status=candidate").json()] == ["Comp 2"]
    assert [c["title"] for c in client.get(f"{API}?status=watching").json()] == ["Comp 1"]
    assert len(client.get(API).json()) == 2
