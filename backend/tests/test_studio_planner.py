"""Planner: status flow, scheduling, linking the published video, outcomes and "what has worked"."""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.studio.models import StudioIdea, StudioIdeaOutcome, StudioMyVideo, StudioVideoTotals
from app.studio.services.planner import mark_published, schedule, set_status, update_outcomes, what_worked
from tests.studio_fakes import make_connection, open_db

TODAY = date(2026, 10, 24)


@pytest.fixture()
def db():
    with open_db() as session:
        yield session


def idea(db, conn, *, status="new", fmt="short", topic="Silent confidence", title="Silent confidence wins"):
    row = StudioIdea(connection_id=conn.id, status=status, format=fmt, topic=topic, payload={"titles": [title]})
    db.add(row)
    db.commit()
    return row


def video(db, conn, vid, *, days_old, views, short=True, as_of=TODAY):
    db.add(StudioMyVideo(connection_id=conn.id, youtube_video_id=vid, title=vid, is_short=short,
                         live_broadcast_content="none",
                         published_at=datetime.combine(TODAY - timedelta(days=days_old), datetime.min.time(), tzinfo=timezone.utc)))
    db.add(StudioVideoTotals(connection_id=conn.id, youtube_video_id=vid, as_of=as_of, views=views, watch_minutes=0,
                             avg_view_duration_s=0, avg_view_pct=50.0, subs_gained=0))
    db.commit()


def baseline_videos(db, conn, *, short=True, vpd=10, n=5):
    for i in range(n):
        video(db, conn, f"{'s' if short else 'l'}base{i}", days_old=20, views=vpd * 20, short=short)


def test_status_transitions(db):
    conn = make_connection(db)
    row = idea(db, conn)
    set_status(db, row, "saved")
    set_status(db, row, "approved")
    with pytest.raises(ValueError):
        set_status(db, row, "new")
    set_status(db, row, "rejected")
    with pytest.raises(ValueError):
        set_status(db, row, "approved")
    with pytest.raises(ValueError):
        set_status(db, idea(db, conn), "published")  # only via mark_published


def test_schedule_sets_date(db):
    conn = make_connection(db)
    row = idea(db, conn)
    schedule(db, row, date(2026, 10, 30))
    assert row.status == "scheduled" and row.planned_date == date(2026, 10, 30)
    schedule(db, row, date(2026, 10, 31))
    assert row.planned_date == date(2026, 10, 31)
    rejected = idea(db, conn, status="rejected")
    with pytest.raises(ValueError):
        schedule(db, rejected, date(2026, 10, 30))


def test_mark_published_rejects_foreign_video(db):
    conn, other = make_connection(db, 1), make_connection(db, 2)
    video(db, other, "theirs", days_old=1, views=5)
    with pytest.raises(ValueError, match="not a video of this channel"):
        mark_published(db, idea(db, conn), "theirs", today=TODAY)


def test_mark_published_twice_rejected(db):
    conn = make_connection(db)
    video(db, conn, "v1", days_old=1, views=5)
    mark_published(db, idea(db, conn), "v1", today=TODAY)
    with pytest.raises(ValueError, match="already linked"):
        mark_published(db, idea(db, conn, topic="Other"), "v1", today=TODAY)


def test_baseline_per_format(db):
    conn = make_connection(db)
    baseline_videos(db, conn, short=True, vpd=10)
    baseline_videos(db, conn, short=False, vpd=99)
    video(db, conn, "new", days_old=1, views=5)
    row = mark_published(db, idea(db, conn), "new", today=TODAY)
    assert row.status == "published" and row.published_video_id == "new" and row.published_at is not None
    assert row.baseline == {"format": "short", "median_vpd": 10.0, "videos": 5}


def test_baseline_none_with_few_videos(db):
    conn = make_connection(db)
    baseline_videos(db, conn, n=4)
    video(db, conn, "new", days_old=1, views=5)
    assert mark_published(db, idea(db, conn), "new", today=TODAY).baseline["median_vpd"] is None


def test_outcome_waits_for_day_7(db):
    conn = make_connection(db)
    baseline_videos(db, conn)
    video(db, conn, "new", days_old=6, views=600)
    mark_published(db, idea(db, conn), "new", today=TODAY)
    assert update_outcomes(db, conn, today=TODAY) == 0
    assert db.query(StudioIdeaOutcome).count() == 0


def test_outcome_verdicts(db):
    conn = make_connection(db)
    baseline_videos(db, conn)  # median 10/day
    for vid, views, verdict in (("hit", 7 * 20, "worked"), ("ok", 7 * 10, "neutral"), ("flop", 7 * 5, "didnt")):
        video(db, conn, vid, days_old=7, views=views)
        mark_published(db, idea(db, conn, topic=vid), vid, today=TODAY)
    assert update_outcomes(db, conn, today=TODAY) == 3
    verdicts = {o.verdict for o in db.query(StudioIdeaOutcome).all()}
    assert verdicts == {"worked", "neutral", "didnt"}
    assert update_outcomes(db, conn, today=TODAY) == 0  # once per checkpoint
    assert update_outcomes(db, conn, today=TODAY + timedelta(days=21)) == 3  # day 28


def test_no_baseline_verdict(db):
    conn = make_connection(db)
    video(db, conn, "new", days_old=7, views=70)
    mark_published(db, idea(db, conn), "new", today=TODAY)
    update_outcomes(db, conn, today=TODAY)
    out = db.query(StudioIdeaOutcome).one()
    assert out.verdict == "no_baseline" and out.ratio is None


def test_what_worked_groups_and_too_few(db):
    conn = make_connection(db)
    baseline_videos(db, conn)
    for i in range(3):
        video(db, conn, f"w{i}", days_old=7, views=7 * 20)
        mark_published(db, idea(db, conn, topic="Silent confidence", title=f"Silent confidence {i} ways"), f"w{i}", today=TODAY)
    video(db, conn, "q", days_old=7, views=7 * 5)
    mark_published(db, idea(db, conn, topic="Burnout", title="Are you burnt out?"), "q", today=TODAY)
    update_outcomes(db, conn, today=TODAY)
    result = what_worked(db, conn)
    groups = {g["group"]: g for g in result["groups"]}
    assert groups["format:short"] == {"group": "format:short", "worked": 3, "neutral": 0, "didnt": 1, "n": 4}
    assert groups["topic:silent"]["worked"] == 3 and groups["pattern:number"]["n"] == 3
    assert "topic:burnout" in result["too_few"] and "pattern:question" in result["too_few"]
