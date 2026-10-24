"""Advisor reports, ideas and idea outcomes: ORM, migration and the AI settings."""

import os
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.studio.models import StudioAdvisorReport, StudioGoogleConnection, StudioIdea, StudioIdeaOutcome
from tests.studio_fakes import make_connection, open_db

BACKEND_DIR = Path(__file__).resolve().parent.parent
HEAD = "f4a5b6c7d8e9"


@pytest.fixture()
def db():
    with open_db() as session:
        yield session


def _idea(conn, report=None, **kw):
    return StudioIdea(connection_id=conn.id, report_id=report.id if report else None, status="new", format="short",
                      topic="Sunday reset", payload={"titles": ["a", "b", "c"]}, **kw)


def test_roundtrip(db):
    conn = make_connection(db)
    report = StudioAdvisorReport(connection_id=conn.id, kind="ideas", evidence_hash="h" * 64, payload={"x": 1}, model="m")
    db.add(report)
    db.commit()
    idea = _idea(conn, report, planned_date=date(2026, 10, 30))
    db.add(idea)
    db.commit()
    db.add(StudioIdeaOutcome(idea_id=idea.id, checkpoint=7, views_per_day=2.0, baseline_vpd=1.0, ratio=2.0, verdict="worked"))
    db.commit()
    again = db.get(StudioIdea, idea.id)
    assert again.payload == {"titles": ["a", "b", "c"]} and again.created_at is not None
    assert again.planned_date == date(2026, 10, 30) and again.baseline is None
    assert db.get(StudioAdvisorReport, report.id).created_at is not None
    assert db.query(StudioIdeaOutcome).one().measured_at is not None


def test_cascade_from_connection_and_idea(db):
    conn = make_connection(db)
    report = StudioAdvisorReport(connection_id=conn.id, kind="advice", evidence_hash="h", payload={}, model="m")
    db.add(report)
    db.commit()
    idea = _idea(conn, report)
    db.add(idea)
    db.commit()
    db.add(StudioIdeaOutcome(idea_id=idea.id, checkpoint=7, views_per_day=1, baseline_vpd=1, ratio=1, verdict="neutral"))
    db.commit()
    db.delete(db.get(StudioIdea, idea.id))
    db.commit()
    assert db.query(StudioIdeaOutcome).count() == 0
    db.add(_idea(conn))
    db.commit()
    db.expire_all()
    db.delete(db.get(StudioGoogleConnection, conn.id))
    db.commit()
    assert db.query(StudioIdea).count() == 0 and db.query(StudioAdvisorReport).count() == 0


def test_outcome_unique_per_checkpoint(db):
    conn = make_connection(db)
    idea = _idea(conn)
    db.add(idea)
    db.commit()
    for _ in range(2):
        db.add(StudioIdeaOutcome(idea_id=idea.id, checkpoint=7, views_per_day=1, baseline_vpd=1, ratio=1, verdict="neutral"))
    with pytest.raises(IntegrityError):
        db.commit()


def _alembic(env, *args):
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND_DIR, env=env, capture_output=True, text=True)


def test_alembic_head_creates_advisor_tables(tmp_path):
    db_file = tmp_path / "mig.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_file}"}
    result = _alembic(env, "upgrade", "head")
    assert result.returncode == 0, result.stderr
    con = sqlite3.connect(db_file)
    try:
        tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        version = con.execute("select version_num from alembic_version").fetchone()[0]
    finally:
        con.close()
    assert {"studio_advisor_report", "studio_idea", "studio_idea_outcome"} <= tables
    assert version == HEAD


def test_advisor_migration_safe_after_create_all(tmp_path):
    db_file = tmp_path / "pre.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_file}"}
    assert _alembic(env, "upgrade", "e3f4a5b6c7d8").returncode == 0
    create_all = subprocess.run([sys.executable, "-c", "from app.db.session import init_db; init_db()"],
                                cwd=BACKEND_DIR, env=env, capture_output=True, text=True)
    assert create_all.returncode == 0, create_all.stderr
    result = _alembic(env, "upgrade", "head")
    assert result.returncode == 0, result.stderr


def test_ai_settings(monkeypatch):
    monkeypatch.setenv("AI_API_KEY", " ")
    monkeypatch.delenv("AI_DAILY_CAP", raising=False)
    get_settings.cache_clear()
    assert get_settings().ai_api_key is None and get_settings().ai_daily_cap == 3
