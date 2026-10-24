"""Impressions + CTR from YouTube Reporting API reach files."""

from datetime import date
from pathlib import Path

import pytest

from app.studio.models import StudioReachDaily
from app.studio.services.reporting_pull import BASE, REACH_TYPE, ensure_reach_job, parse_reach_csv, pull_reach
from tests.studio_fakes import FakeSession, make_connection, open_db

CSV = (Path(__file__).parent / "fixtures" / "reach_basic_a1.csv").read_text(encoding="utf-8")


@pytest.fixture()
def db():
    with open_db() as session:
        yield session


def test_ensure_job_reuses_existing_job(db):
    conn = make_connection(db)
    session = FakeSession([(f"{BASE}/jobs", None, {"jobs": [
        {"id": "other", "reportTypeId": "channel_basic_a3"}, {"id": "j1", "reportTypeId": REACH_TYPE}]})])
    assert ensure_reach_job(db, conn, session) == "j1"
    assert conn.reach_job_id == "j1"
    assert ensure_reach_job(db, conn, session) == "j1"
    assert len(session.calls) == 1  # stored id reused, no second listing


def test_ensure_job_creates_when_missing(db):
    conn = make_connection(db)
    session = FakeSession()
    session.routes = [(f"{BASE}/jobs", None, lambda p: {"id": "new"} if p and "reportTypeId" in p else {})]
    assert ensure_reach_job(db, conn, session) == "new"
    method, _, body = session.calls[-1]
    assert method == "POST" and body == {"reportTypeId": REACH_TYPE, "name": "YT Growth Studio reach"}


def test_parse_sums_across_extra_dimensions():
    parsed = parse_reach_csv(CSV)
    assert parsed[(date(2026, 10, 20), "v1")] == (400, pytest.approx(100 * 0.05 + 300 * 0.02))
    assert parsed[(date(2026, 10, 20), "v2")] == (50, pytest.approx(5.0))
    assert parsed[(date(2026, 10, 21), "v1")] == (10, 0.0)
    assert len(parsed) == 3  # the row without a video id is skipped


def test_parse_missing_column_raises():
    with pytest.raises(ValueError, match="video_thumbnail_impressions_ctr"):
        parse_reach_csv("date,video_id,video_thumbnail_impressions\n20261020,v1,5\n")


def _reports_session(reports, texts):
    return FakeSession([(f"{BASE}/jobs/j1/reports", None, {"reports": reports})], texts=texts)


def test_pull_skips_processed_reports(db):
    conn = make_connection(db, reach_job_id="j1")
    reports = [{"id": "r1", "downloadUrl": "https://dl/r1"}]
    assert pull_reach(db, conn, _reports_session(reports, {"https://dl/r1": CSV})) == {"files": 1, "rows": 3}
    session = _reports_session(reports, {})
    assert pull_reach(db, conn, session) == {"files": 0, "rows": 0}
    assert [c for c in session.calls if c[1].startswith("https://dl/")] == []
    assert conn.reach_report_ids == ["r1"]
    assert db.query(StudioReachDaily).count() == 3


def test_pull_follows_next_page_token(db):
    conn = make_connection(db, reach_job_id="j1")
    session = FakeSession([
        (f"{BASE}/jobs/j1/reports", {"pageToken": "p2"}, {"reports": [{"id": "r2", "downloadUrl": "https://dl/r2"}]}),
        (f"{BASE}/jobs/j1/reports", None, {"reports": [{"id": "r1", "downloadUrl": "https://dl/r1"}], "nextPageToken": "p2"}),
    ], texts={"https://dl/r1": CSV, "https://dl/r2": CSV})
    assert pull_reach(db, conn, session)["files"] == 2


def test_pull_no_reports_yet_is_ok(db):
    conn = make_connection(db, reach_job_id="j1")
    assert pull_reach(db, conn, _reports_session([], {})) == {"files": 0, "rows": 0}


def test_later_file_replaces_same_day_video(db):
    conn = make_connection(db, reach_job_id="j1")
    newer = "date,video_id,video_thumbnail_impressions,video_thumbnail_impressions_ctr\n20261020,v1,1000,0.1\n"
    reports = [{"id": "r1", "downloadUrl": "https://dl/r1"}, {"id": "r2", "downloadUrl": "https://dl/r2"}]
    pull_reach(db, conn, _reports_session(reports, {"https://dl/r1": CSV, "https://dl/r2": newer}))
    row = db.query(StudioReachDaily).filter_by(day=date(2026, 10, 20), youtube_video_id="v1").one()
    assert (row.impressions, row.clicks) == (1000, pytest.approx(100.0))
