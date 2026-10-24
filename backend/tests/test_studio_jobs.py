"""Studio jobs, missed-run catch-up and the auto-discovery toggle."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import JobRun
from app.db.session import SessionLocal, init_db
from app.studio import jobs
from app.studio.services.discovery import DiscoveryResult
from app.studio.services.settings_store import get_auto_discovery, set_auto_discovery

NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


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


def last_run(name):
    with SessionLocal() as db:
        return db.scalars(select(JobRun).where(JobRun.job_name == name).order_by(JobRun.id.desc())).first()


def test_sync_job_logs_jobrun_success(db, monkeypatch):
    monkeypatch.setattr(jobs, "sync_all_watching", lambda session: [{"competitor": "UCx", "videos_upserted": 3}])
    out = jobs.competitor_sync_job()
    run = last_run("studio_competitor_sync")
    assert run.status == "success" and run.finished_at is not None
    assert run.detail == {"results": [{"competitor": "UCx", "videos_upserted": 3}]}
    assert out["results"][0]["videos_upserted"] == 3


def test_sync_job_logs_jobrun_failure(db, monkeypatch):
    def boom(session):
        raise RuntimeError("quota gone")

    monkeypatch.setattr(jobs, "sync_all_watching", boom)
    jobs.competitor_sync_job()
    run = last_run("studio_competitor_sync")
    assert run.status == "failed" and "quota gone" in run.detail["error"]


def test_discovery_job_skips_when_auto_discovery_off_and_spends_nothing(db, monkeypatch):
    def must_not_run(*args, **kwargs):
        raise AssertionError("discover() must not run while auto-discovery is off")

    monkeypatch.setattr(jobs, "discover", must_not_run)
    out = jobs.competitor_discovery_job()
    run = last_run("studio_competitor_discovery")
    assert run.status == "skipped" and run.detail == {"skipped": "auto_discovery is off"}
    assert out["skipped"] == "auto_discovery is off"


def test_discovery_job_runs_when_on(db, monkeypatch):
    set_auto_discovery(db, True)
    monkeypatch.setattr(jobs, "discover", lambda *a, **k: DiscoveryResult(added=2, searches_used=7, competitor_ids=[1, 2]))
    jobs.competitor_discovery_job()
    run = last_run("studio_competitor_discovery")
    assert run.status == "success" and run.detail["added"] == 2 and run.detail["searches_used"] == 7


def test_discovery_job_records_failure(db, monkeypatch):
    set_auto_discovery(db, True)

    def boom(*a, **k):
        raise ValueError("Pick your channel in Studio settings first")

    monkeypatch.setattr(jobs, "discover", boom)
    jobs.competitor_discovery_job()
    run = last_run("studio_competitor_discovery")
    assert run.status == "failed" and "Pick your channel" in run.detail["error"]


def _run(db, name, status, hours_ago):
    db.add(JobRun(job_name=name, status=status, started_at=NOW - timedelta(hours=hours_ago)))
    db.commit()


def test_job_overdue_cases(db):
    day = timedelta(hours=24)
    assert jobs.job_overdue(db, "j", day, now=NOW) is True                    # never ran
    _run(db, "j", "failed", 1)
    _run(db, "j", "skipped", 1)
    assert jobs.job_overdue(db, "j", day, now=NOW) is True                    # only failed / skipped runs
    _run(db, "j", "success", 30)
    assert jobs.job_overdue(db, "j", day, now=NOW) is True                    # last success 30 h ago
    _run(db, "j", "success", 2)
    assert jobs.job_overdue(db, "j", day, now=NOW) is False                   # last success 2 h ago
    assert jobs.job_overdue(db, "other", day, now=NOW) is True                # other jobs are independent


def test_auto_discovery_default_false_and_toggle_via_api(client, db):
    assert get_auto_discovery(db) is False
    assert client.get("/api/v1/studio/status").json()["auto_discovery"] is False
    res = client.put("/api/v1/studio/auto-discovery", json={"enabled": True})
    assert res.status_code == 200 and res.json() == {"enabled": True}
    assert client.get("/api/v1/studio/status").json()["auto_discovery"] is True
    assert client.put("/api/v1/studio/auto-discovery", json={"enabled": "maybe"}).status_code == 422


def test_scheduler_registers_studio_jobs(monkeypatch):
    from app.jobs import scheduler as scheduler_module

    init_db()
    monkeypatch.setenv("ENABLE_SCHEDULER", "true")
    get_settings.cache_clear()
    sched = scheduler_module.start_scheduler()
    try:
        assert sched is not None
        by_id = {j.id: j for j in sched.get_jobs()}
        assert {"studio_competitor_sync", "studio_competitor_discovery"} <= set(by_id)
        # nothing has ever run, so both are overdue and scheduled shortly after start (catch-up)
        for job_id in ("studio_competitor_sync", "studio_competitor_discovery"):
            assert by_id[job_id].next_run_time is not None
            assert by_id[job_id].next_run_time - datetime.now(timezone.utc) < timedelta(minutes=2)
    finally:
        scheduler_module.stop_scheduler()


def test_my_channels_job_continues_after_one_failure(db, monkeypatch):
    from app.studio.services import my_channel
    from tests.studio_fakes import make_connection

    first = make_connection(db, 1)
    make_connection(db, 2)
    make_connection(db, 3, status="disconnected")
    seen = []

    def fake_pull(session_db, conn, **kw):
        seen.append(conn.id)
        if conn.id == first.id:
            raise RuntimeError("boom")
        return {"connection_id": conn.id}

    monkeypatch.setattr(my_channel, "pull_connection", fake_pull)
    result = jobs.my_channels_pull_job()
    assert len(seen) == 2  # the disconnected channel is skipped
    assert result["results"][0] == {"connection_id": first.id, "error": "boom"}
    assert last_run(jobs.MY_CHANNELS_JOB).status == "success"
    db.expire_all()
    assert db.get(type(first), first.id).last_error == "boom"


def test_advisor_job_updates_outcomes_without_ai(db, monkeypatch):
    from tests.studio_fakes import make_connection

    conn = make_connection(db, 1)
    make_connection(db, 2, status="disconnected")
    seen = []
    monkeypatch.setattr(jobs, "update_outcomes", lambda session_db, c, **kw: seen.append(c.id) or 0)
    monkeypatch.setenv("AI_API_KEY", "")
    get_settings.cache_clear()
    result = jobs.advisor_daily_job()
    assert seen == [conn.id]
    assert result["results"] == [{"connection_id": conn.id, "outcomes": 0, "ai": "not configured"}]
    assert last_run(jobs.ADVISOR_JOB).status == "success"


def test_advisor_job_generates_once_per_day_and_isolates_failures(db, monkeypatch):
    from tests.studio_fakes import make_connection

    first, second = make_connection(db, 1), make_connection(db, 2)
    monkeypatch.setenv("AI_API_KEY", "sk-test")
    get_settings.cache_clear()
    monkeypatch.setattr(jobs, "update_outcomes", lambda *a, **k: 0)
    monkeypatch.setattr(jobs, "build_evidence", lambda *a, **k: {"items": []})
    calls = []

    def advice(session_db, conn, ai, pack, **kw):
        calls.append(("advice", conn.id))
        if conn.id == first.id:
            raise RuntimeError("provider down")

    monkeypatch.setattr(jobs, "generate_advice", advice)
    monkeypatch.setattr(jobs, "generate_ideas", lambda session_db, conn, ai, pack, **kw: calls.append(("ideas", conn.id)) or [])
    result = jobs.advisor_daily_job()
    assert ("advice", second.id) in calls and ("ideas", second.id) in calls and ("ideas", first.id) in calls
    assert result["results"][0]["advice_error"] == "provider down"
