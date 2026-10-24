"""Which public channel is the owner's own (used by compare + discovery)."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.models import Channel
from app.db.session import SessionLocal, init_db
from app.studio.services.own_channel import get_own_channel, set_own_channel


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


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("SERPAPIKEY", raising=False)
    get_settings.cache_clear()


def _channel(db, n: int, *, archived: bool = False, subs: int | None = 1000) -> Channel:
    ch = Channel(
        youtube_channel_id=f"UC{n:022d}",
        title=f"Channel {n}",
        subscriber_count=subs,
        archived_at=datetime.now(timezone.utc) if archived else None,
    )
    db.add(ch)
    db.commit()
    db.refresh(ch)
    return ch


def test_single_channel_is_picked_automatically(db):
    only = _channel(db, 1)
    assert get_own_channel(db).id == only.id


def test_two_channels_and_nothing_saved_returns_none(db):
    _channel(db, 1)
    _channel(db, 2)
    assert get_own_channel(db) is None


def test_set_then_get(db):
    _channel(db, 1)
    second = _channel(db, 2)
    assert set_own_channel(db, second.id).id == second.id
    assert get_own_channel(db).id == second.id


def test_archived_saved_channel_is_ignored(db):
    keep = _channel(db, 1)
    gone = _channel(db, 2)
    set_own_channel(db, gone.id)
    gone.archived_at = datetime.now(timezone.utc)
    db.commit()
    assert get_own_channel(db).id == keep.id  # falls back to the single active channel


def test_set_unknown_channel_raises_valueerror(db):
    with pytest.raises(ValueError):
        set_own_channel(db, 999)


def test_set_archived_channel_raises_valueerror(db):
    ch = _channel(db, 1, archived=True)
    with pytest.raises(ValueError):
        set_own_channel(db, ch.id)


def test_get_own_channel_api_lists_candidates(client, db):
    a = _channel(db, 1)
    b = _channel(db, 2)
    body = client.get("/api/v1/studio/own-channel").json()
    assert body["channel"] is None
    assert {c["id"] for c in body["candidates"]} == {a.id, b.id}


def test_put_own_channel_api_roundtrip(client, db):
    a = _channel(db, 1, subs=None)
    _channel(db, 2)
    res = client.put("/api/v1/studio/own-channel", json={"channel_id": a.id})
    assert res.status_code == 200
    assert res.json()["channel"] == {"id": a.id, "title": "Channel 1", "subscriber_count": None}


def test_put_own_channel_422_for_unknown_id(client):
    res = client.put("/api/v1/studio/own-channel", json={"channel_id": 12345})
    assert res.status_code == 422


def test_status_includes_own_channel(client, db):
    a = _channel(db, 1)
    status = client.get("/api/v1/studio/status").json()
    assert status["own_channel"] == {"id": a.id, "title": "Channel 1"}
