"""Advisor: six answers validated against the evidence pack, cached per evidence hash and day."""

import json
from datetime import date

import pytest

from app.studio.models import StudioAdvisorReport
from app.studio.services.advisor import (
    QUESTIONS,
    AdviceInProgress,
    AdviceRejected,
    generate_advice,
    latest_advice,
    report_lock,
)
from tests.studio_fakes import make_connection, open_db

TODAY = date(2026, 10, 24)
PACK = {
    "channel": {"title": "AXIOM", "format_mix": {"short": 10, "long": 3}},
    "items": [
        {"id": "F:traffic_mix", "kind": "finding", "text": "75% from Shorts feed", "data": {}},
        {"id": "P:views", "kind": "progress", "text": "views down 57%", "data": {}},
    ],
    "own_titles": ["Own title"],
    "earlier_ideas": [],
    "notes": ["No competitors are being watched yet."],
}


class FakeAI:
    configured = True

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def complete_json(self, system, user):
        self.calls.append((system, user))
        return self.reply, "fake-model"


def _answers(**overrides):
    answers = [{"question": q, "answer": f"Answer for {q}", "evidence_ids": ["F:traffic_mix"], "confidence": "medium"}
               for q in QUESTIONS]
    for a in answers:
        a.update(overrides.get(a["question"], {}))
    return {"answers": answers}


@pytest.fixture()
def db():
    with open_db() as session:
        yield session


def test_valid_reply_stored(db):
    conn = make_connection(db)
    report = generate_advice(db, conn, FakeAI(_answers()), PACK, today=TODAY)
    assert report.kind == "advice" and report.model == "fake-model" and len(report.evidence_hash) == 64
    assert [a["question"] for a in report.payload["answers"]] == QUESTIONS
    assert latest_advice(db, conn).id == report.id


def test_unknown_evidence_id_rejected(db):
    conn = make_connection(db)
    ai = FakeAI(_answers(mistakes={"evidence_ids": ["F:traffic_mix", "B:made_up"]}))
    with pytest.raises(AdviceRejected, match="B:made_up"):
        generate_advice(db, conn, ai, PACK, today=TODAY)
    assert db.query(StudioAdvisorReport).count() == 0


def test_empty_evidence_ids_rejected(db):
    conn = make_connection(db)
    with pytest.raises(AdviceRejected):
        generate_advice(db, conn, FakeAI(_answers(mistakes={"evidence_ids": []})), PACK, today=TODAY)


def test_missing_question_rejected(db):
    conn = make_connection(db)
    reply = _answers()
    reply["answers"] = reply["answers"][:5]
    with pytest.raises(AdviceRejected, match="focus_this_week"):
        generate_advice(db, conn, FakeAI(reply), PACK, today=TODAY)


def test_bad_shape_rejected(db):
    conn = make_connection(db)
    with pytest.raises(AdviceRejected):
        generate_advice(db, conn, FakeAI({"answer": "everything is fine"}), PACK, today=TODAY)


def test_same_hash_same_day_reuses_without_call(db):
    conn = make_connection(db)
    ai = FakeAI(_answers())
    first = generate_advice(db, conn, ai, PACK, today=TODAY)
    again = generate_advice(db, conn, ai, PACK, today=TODAY)
    assert again.id == first.id and len(ai.calls) == 1


def test_force_calls_again(db):
    conn = make_connection(db)
    ai = FakeAI(_answers())
    generate_advice(db, conn, ai, PACK, today=TODAY)
    generate_advice(db, conn, ai, PACK, today=TODAY, force=True)
    assert len(ai.calls) == 2 and db.query(StudioAdvisorReport).count() == 2


def test_prompt_contains_only_pack(db):
    conn = make_connection(db)
    ai = FakeAI(_answers())
    generate_advice(db, conn, ai, PACK, today=TODAY)
    system, user = ai.calls[0]
    sent = json.loads(user)
    assert set(sent) == {"channel", "evidence", "notes", "questions"}
    assert [i["id"] for i in sent["evidence"]] == ["F:traffic_mix", "P:views"]
    conn.refresh_token = "secret-refresh-token-xyz"
    assert "secret-refresh-token-xyz" not in user and "refresh" not in user
    assert "evidence_ids" in system and "Shorts" in system


def test_busy_lock_raises(db):
    conn = make_connection(db)
    lock = report_lock("advice", conn.id)
    lock.acquire()
    try:
        with pytest.raises(AdviceInProgress):
            generate_advice(db, conn, FakeAI(_answers()), PACK, today=TODAY)
    finally:
        lock.release()
