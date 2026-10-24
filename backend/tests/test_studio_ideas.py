"""Daily ideas: validation against the pack, duplicate guard, upload time only from the code's finding."""

import copy
from datetime import date

import pytest

from app.studio.models import StudioAdvisorReport, StudioIdea
from app.studio.services.ideas import IdeasRejected, generate_ideas, is_duplicate, overlap
from tests.studio_fakes import make_connection, open_db

TODAY = date(2026, 10, 24)


def _pack(timing=None):
    items = [
        {"id": "B:abc123", "kind": "competitor_breakout", "text": "breakout", "data": {}},
        {"id": "F:publish_timing", "kind": "finding", "text": "timing",
         "data": timing or {"status": "inconclusive", "evidence": {}}},
        {"id": "GAP:quiet", "kind": "topic_gap", "text": "gap", "data": {}},
    ]
    return {"channel": {"title": "AXIOM", "format_mix": {"short": 5, "long": 1}}, "items": items,
            "own_titles": ["Work Burnout Signs: You're Not Lazy, You're Empty #Shorts"],
            "earlier_ideas": ["Quiet mornings — How quiet mornings changed my focus"], "notes": []}


def _idea(topic="Silent confidence", title="Silent Confidence: Stop Explaining Yourself", fmt="short", **kw):
    idea = {
        "topic": topic, "reason": "Competitor breakout and a gap", "format": fmt,
        "titles": [title, "Quiet people win", "Why silence is power"],
        "hook": "Stop explaining yourself.", "outline": ["hook", "point", "close"], "duration_minutes": 0.5,
        "description": "A short about silent confidence.", "hashtags": ["#confidence"], "tags": ["confidence"],
        "competitor_refs": ["B:abc123"], "evidence_ids": ["GAP:quiet"], "confidence": "medium", "priority": 2,
    }
    idea.update(kw)
    return idea


class FakeAI:
    configured = True

    def __init__(self, ideas):
        self.reply = {"ideas": ideas}
        self.calls = 0

    def complete_json(self, system, user):
        self.calls += 1
        return copy.deepcopy(self.reply), "fake-model"


@pytest.fixture()
def db():
    with open_db() as session:
        yield session


def test_overlap_ignores_hashtags_and_stopwords():
    assert overlap("Work burnout signs #shorts", "Burnout signs at work") == 1.0
    assert overlap("Morning routine", "Evening walk") == 0.0
    assert overlap("#shorts #motivation", "anything") == 0.0  # nothing left to compare


def test_duplicate_of_own_title_skipped(db):
    conn = make_connection(db)
    ideas = [_idea(), _idea(topic="Burnout", title="Burnout Signs At Work You Ignore")]
    created = generate_ideas(db, conn, FakeAI(ideas), _pack(), today=TODAY)
    assert [i.topic for i in created] == ["Silent confidence"]
    assert db.query(StudioAdvisorReport).one().payload["skipped_duplicates"] == 1


def test_duplicate_of_earlier_idea_skipped(db):
    conn = make_connection(db)
    ideas = [_idea(topic="Quiet mornings", title="Quiet Mornings Changed My Focus")]
    assert generate_ideas(db, conn, FakeAI(ideas), _pack(), today=TODAY) == []


def test_duplicates_within_one_batch_skipped(db):
    conn = make_connection(db)
    created = generate_ideas(db, conn, FakeAI([_idea(), _idea()]), _pack(), today=TODAY)
    assert len(created) == 1


def test_titles_count_validated(db):
    conn = make_connection(db)
    with pytest.raises(IdeasRejected):
        generate_ideas(db, conn, FakeAI([_idea(titles=["only one"])]), _pack(), today=TODAY)
    assert db.query(StudioIdea).count() == 0


def test_competitor_ref_must_be_breakout_id(db):
    conn = make_connection(db)
    with pytest.raises(IdeasRejected, match="GAP:quiet"):
        generate_ideas(db, conn, FakeAI([_idea(competitor_refs=["GAP:quiet"])]), _pack(), today=TODAY)
    with pytest.raises(IdeasRejected, match="B:nope"):
        generate_ideas(db, conn, FakeAI([_idea(evidence_ids=["B:nope"])]), _pack(), today=TODAY)


def test_upload_time_only_from_good_finding_same_format(db):
    conn = make_connection(db)
    good_shorts = {"status": "good", "evidence": {"format": "Shorts", "best_day": "Friday"}}
    ideas = [_idea(), _idea(topic="Long talk", title="A Long Talk About Patience", fmt="long", duration_minutes=8)]
    created = {i.format: i for i in generate_ideas(db, conn, FakeAI(ideas), _pack(good_shorts), today=TODAY)}
    assert "Friday" in created["short"].payload["upload_time"]
    assert created["long"].payload["upload_time"] == "no reliable data"


def test_ideas_stored_with_status_new(db):
    conn = make_connection(db)
    ai = FakeAI([_idea()])
    created = generate_ideas(db, conn, ai, _pack(), today=TODAY)
    idea = created[0]
    assert idea.status == "new" and idea.format == "short" and idea.report_id is not None
    assert idea.payload["titles"][0] == "Silent Confidence: Stop Explaining Yourself"
    again = generate_ideas(db, conn, ai, _pack(), today=TODAY)  # same pack, same day: cached
    assert ai.calls == 1 and [i.id for i in again] == [idea.id]


def test_is_duplicate_threshold():
    assert is_duplicate("Silent confidence at work", ["Silent nights"]) is False  # 1 of 3 words
    assert is_duplicate("Silent confidence at work", ["Silent confidence"]) is True  # 2 of 3 = 0.67
    assert is_duplicate("Silent confidence", ["Silent confidence at work"]) is True
