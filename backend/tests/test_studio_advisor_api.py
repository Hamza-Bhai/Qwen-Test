"""Advisor and ideas API: shapes, no-key 409, rejected 422, cap 429, planner errors."""

from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.db.session import SessionLocal, init_db
from app.studio.models import StudioIdea, StudioMyVideo
from app.studio.services.ai_client import AIBudgetExceeded, AINotConfigured
from app.studio.services.advisor import QUESTIONS
from tests.studio_fakes import make_connection

API = "/api/v1/studio"
PACK = {
    "channel": {"title": "Mine 1", "format_mix": {"short": 1, "long": 0}},
    "items": [{"id": "F:traffic_mix", "kind": "finding", "text": "75% from Shorts", "data": {}},
              {"id": "B:abc", "kind": "competitor_breakout", "text": "breakout", "data": {}}],
    "own_titles": [], "earlier_ideas": [], "notes": [],
}
ADVICE = {"answers": [{"question": q, "answer": "a", "evidence_ids": ["F:traffic_mix"], "confidence": "low"}
                      for q in QUESTIONS]}
IDEA = {"topic": "Silent confidence", "reason": "r", "format": "short", "titles": ["t1 silent", "t2", "t3"],
        "hook": "h", "outline": ["a", "b", "c"], "duration_minutes": 0.5, "description": "d", "hashtags": [],
        "tags": [], "competitor_refs": ["B:abc"], "evidence_ids": ["F:traffic_mix"], "confidence": "low", "priority": 3}


class FakeAI:
    def __init__(self, reply=None, error=None, configured=True):
        self.reply, self.error, self.configured = reply, error, configured
        self.cap = 3

    def used_today(self):
        return 1

    def complete_json(self, system, user):
        if self.error:
            raise self.error
        return self.reply, "fake-model"


@pytest.fixture()
def client(monkeypatch):
    from app.main import app

    monkeypatch.setattr("app.studio.routes.advisor.build_evidence", lambda db, conn, **kw: PACK)
    init_db()
    with TestClient(app) as c:
        yield c


def use_ai(monkeypatch, ai):
    monkeypatch.setattr("app.studio.routes.advisor.AIClient", lambda db: ai)


def _conn():
    with SessionLocal() as db:
        return make_connection(db).id


def test_advisor_get_without_advice(client, monkeypatch):
    use_ai(monkeypatch, FakeAI(configured=False))
    cid = _conn()
    body = client.get(f"{API}/advisor/{cid}").json()
    assert body["ai"] == {"configured": False, "used_today": 1, "cap": 3}
    assert body["advice"] is None and len(body["findings"]) == 9 and body["questions"][QUESTIONS[0]]


def test_generate_advice_409_without_key(client, monkeypatch):
    use_ai(monkeypatch, FakeAI(error=AINotConfigured("AI is not configured: add AI_API_KEY"), configured=False))
    r = client.post(f"{API}/advisor/{_conn()}/generate")
    assert r.status_code == 409 and "AI_API_KEY" in r.json()["detail"]


def test_generate_advice_and_read_back(client, monkeypatch):
    use_ai(monkeypatch, FakeAI(ADVICE))
    cid = _conn()
    r = client.post(f"{API}/advisor/{cid}/generate")
    assert r.status_code == 200 and len(r.json()["answers"]) == 6
    body = client.get(f"{API}/advisor/{cid}").json()
    assert body["advice"]["model"] == "fake-model"
    assert {e["id"] for e in body["advice"]["evidence"]} == {"F:traffic_mix", "B:abc"}


def test_rejected_reply_is_422(client, monkeypatch):
    bad = {"answers": ADVICE["answers"][:5]}
    use_ai(monkeypatch, FakeAI(bad))
    assert client.post(f"{API}/advisor/{_conn()}/generate").status_code == 422


def test_cap_is_429(client, monkeypatch):
    use_ai(monkeypatch, FakeAI(error=AIBudgetExceeded("Daily AI limit reached (3/3 calls today).")))
    assert client.post(f"{API}/ideas/{_conn()}/generate").status_code == 429


def test_generate_and_list_ideas(client, monkeypatch):
    use_ai(monkeypatch, FakeAI({"ideas": [IDEA]}))
    cid = _conn()
    r = client.post(f"{API}/ideas/{cid}/generate")
    assert r.status_code == 200 and r.json()[0]["topic"] == "Silent confidence"
    listed = client.get(f"{API}/ideas/{cid}").json()
    assert listed[0]["status"] == "new" and listed[0]["outcomes"] == [] and listed[0]["payload"]["titles"][0] == "t1 silent"
    assert client.get(f"{API}/ideas/{cid}", params={"status": "approved"}).json() == []


def _idea(cid, status="new"):
    with SessionLocal() as db:
        row = StudioIdea(connection_id=cid, status=status, format="short", topic="T", payload={"titles": ["t"]})
        db.add(row)
        db.commit()
        return row.id


def test_patch_status_and_schedule(client):
    cid = _conn()
    iid = _idea(cid)
    assert client.patch(f"{API}/ideas/item/{iid}", json={"status": "approved"}).json()["status"] == "approved"
    r = client.patch(f"{API}/ideas/item/{iid}", json={"planned_date": "2026-11-02"})
    assert r.json()["status"] == "scheduled" and r.json()["planned_date"] == "2026-11-02"
    assert client.patch(f"{API}/ideas/item/{iid}", json={"status": "new"}).status_code == 422
    assert client.patch(f"{API}/ideas/item/999", json={"status": "saved"}).status_code == 404


def test_published_foreign_video_422(client):
    cid = _conn()
    iid = _idea(cid)
    r = client.post(f"{API}/ideas/item/{iid}/published", json={"youtube_video_id": "nope"})
    assert r.status_code == 422


def test_published_and_what_worked(client):
    cid = _conn()
    iid = _idea(cid)
    with SessionLocal() as db:
        db.add(StudioMyVideo(connection_id=cid, youtube_video_id="v1", title="t", is_short=True,
                             published_at=datetime(2026, 10, 1, tzinfo=timezone.utc)))
        db.commit()
    r = client.post(f"{API}/ideas/item/{iid}/published", json={"youtube_video_id": "v1"})
    assert r.status_code == 200 and r.json()["status"] == "published"
    worked = client.get(f"{API}/ideas/{cid}/what-worked").json()
    assert worked["groups"] == [] and "caveat" in worked
