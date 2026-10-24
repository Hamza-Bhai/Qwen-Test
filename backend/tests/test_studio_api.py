"""/api/v1/studio status + keywords endpoints."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.session import init_db


@pytest.fixture()
def client():
    from app.main import app

    init_db()
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("SERPAPIKEY", raising=False)
    monkeypatch.delenv("SERPAPI_MONTHLY_CAP", raising=False)
    get_settings.cache_clear()


def test_status_without_key(client):
    body = client.get("/api/v1/studio/status").json()
    assert body["serpapi"]["configured"] is False
    assert body["serpapi"]["used"] == 0
    assert body["serpapi"]["cap"] == 200
    assert body["serpapi"]["remaining"] == 200
    assert body["keywords"] == ["sunday reset", "discipline", "morning routine", "self improvement"]


def test_status_with_key_never_exposes_it(client, monkeypatch):
    monkeypatch.setenv("SERPAPIKEY", "SECRETVALUE")
    get_settings.cache_clear()
    res = client.get("/api/v1/studio/status")
    assert res.json()["serpapi"]["configured"] is True
    assert "SECRETVALUE" not in res.text


def test_status_reflects_cap_env(client, monkeypatch):
    monkeypatch.setenv("SERPAPI_MONTHLY_CAP", "50")
    get_settings.cache_clear()
    serpapi = client.get("/api/v1/studio/status").json()["serpapi"]
    assert serpapi["cap"] == 50 and serpapi["remaining"] == 50


def test_put_keywords_normalises_and_persists(client):
    res = client.put("/api/v1/studio/keywords", json={"keywords": ["  Calm   Mind ", "calm mind", "Focus"]})
    assert res.status_code == 200
    assert res.json() == {"keywords": ["calm mind", "focus"]}
    assert client.get("/api/v1/studio/keywords").json() == {"keywords": ["calm mind", "focus"]}
    assert client.get("/api/v1/studio/status").json()["keywords"] == ["calm mind", "focus"]


def test_put_invalid_keywords_422_and_unchanged(client):
    client.put("/api/v1/studio/keywords", json={"keywords": ["keep"]})
    res = client.put("/api/v1/studio/keywords", json={"keywords": []})
    assert res.status_code == 422
    assert "at least one keyword" in res.json()["detail"].lower()
    assert client.get("/api/v1/studio/keywords").json() == {"keywords": ["keep"]}


def test_put_rejects_non_list_422(client):
    assert client.put("/api/v1/studio/keywords", json={"keywords": "focus"}).status_code == 422
    assert client.put("/api/v1/studio/keywords", json={}).status_code == 422


def test_public_channel_routes_still_work(client):
    assert client.get("/api/v1/channels").status_code == 200
