"""My-channel tables (connected own channels and their private analytics), migration and settings."""

import os
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.db.session import SessionLocal, init_db
from app.studio.models import (
    StudioChannelDaily,
    StudioGoogleConnection,
    StudioMyVideo,
    StudioReachDaily,
    StudioRetention,
    StudioTrafficDaily,
    StudioVideoTotals,
)

BACKEND_DIR = Path(__file__).resolve().parent.parent
HEAD = "e3f4a5b6c7d8"
NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)
DAY = date(2026, 9, 30)
CHILDREN = (StudioMyVideo, StudioChannelDaily, StudioVideoTotals, StudioTrafficDaily, StudioRetention, StudioReachDaily)


@pytest.fixture()
def db():
    init_db()
    with SessionLocal() as session:
        yield session


def _connection(n=1, **kw):
    return StudioGoogleConnection(
        youtube_channel_id=f"UC{n:022d}", title=f"Mine {n}", refresh_token="rt-secret",
        scopes="a b", status="connected", **kw
    )


def _with_children(conn):
    conn_id = conn.id
    return [
        StudioMyVideo(connection_id=conn_id, youtube_video_id="v1", title="t", published_at=NOW, is_short=False),
        StudioChannelDaily(
            connection_id=conn_id, day=DAY, views=10, watch_minutes=20, avg_view_duration_s=30,
            avg_view_pct=40.5, subs_gained=1, subs_lost=0, likes=2, comments=0, shares=0,
        ),
        StudioVideoTotals(
            connection_id=conn_id, youtube_video_id="v1", as_of=DAY, views=10, watch_minutes=20,
            avg_view_duration_s=30, avg_view_pct=40.5, subs_gained=1,
        ),
        StudioTrafficDaily(connection_id=conn_id, day=DAY, source="YT_SEARCH", views=5, watch_minutes=6),
        StudioRetention(connection_id=conn_id, youtube_video_id="v1", elapsed_ratio=0.1, watch_ratio=0.8, pulled_at=NOW),
        StudioReachDaily(connection_id=conn_id, day=DAY, youtube_video_id="v1", impressions=100, clicks=4.5),
    ]


def test_connection_roundtrip_with_children(db):
    conn = _connection(1)
    db.add(conn)
    db.commit()
    db.add_all(_with_children(conn))
    db.commit()

    again = db.get(StudioGoogleConnection, conn.id)
    assert again.connected_at is not None and again.reach_report_ids == []
    assert again.last_pulled_at is None and again.reach_job_id is None
    for model in CHILDREN:
        assert db.query(model).count() == 1
    assert db.query(StudioReachDaily).one().clicks == 4.5
    assert db.query(StudioChannelDaily).one().avg_view_pct == 40.5


def test_deleting_connection_cascades(db):
    conn = _connection(1)
    db.add(conn)
    db.commit()
    db.add_all(_with_children(conn))
    db.commit()
    db.expire_all()
    db.delete(db.get(StudioGoogleConnection, conn.id))
    db.commit()
    for model in CHILDREN:
        assert db.query(model).count() == 0


def test_connection_channel_id_unique(db):
    db.add(_connection(1))
    db.commit()
    db.add(_connection(1))
    with pytest.raises(IntegrityError):
        db.commit()


def test_channel_daily_unique_per_day(db):
    conn = _connection(1)
    db.add(conn)
    db.commit()
    for _ in range(2):
        db.add(StudioChannelDaily(
            connection_id=conn.id, day=DAY, views=1, watch_minutes=1, avg_view_duration_s=1,
            avg_view_pct=1.0, subs_gained=0, subs_lost=0, likes=0, comments=0, shares=0,
        ))
    with pytest.raises(IntegrityError):
        db.commit()


def _alembic(env, *args):
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=BACKEND_DIR, env=env, capture_output=True, text=True
    )


def test_alembic_head_creates_my_channel_tables(tmp_path):
    db_file = tmp_path / "mig.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_file}"}
    result = _alembic(env, "upgrade", HEAD)  # pinned: head moved on in milestone D
    assert result.returncode == 0, result.stderr
    con = sqlite3.connect(db_file)
    try:
        tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        version = con.execute("select version_num from alembic_version").fetchone()[0]
    finally:
        con.close()
    assert {m.__tablename__ for m in (StudioGoogleConnection, *CHILDREN)} <= tables
    assert version == HEAD


def test_my_channel_migration_safe_after_create_all(tmp_path):
    db_file = tmp_path / "pre.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_file}"}
    assert _alembic(env, "upgrade", "d2e3f4a5b6c7").returncode == 0
    create_all = subprocess.run(
        [sys.executable, "-c", "from app.db.session import init_db; init_db()"],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True,
    )
    assert create_all.returncode == 0, create_all.stderr
    result = _alembic(env, "upgrade", "head")
    assert result.returncode == 0, result.stderr


def test_blank_oauth_client_id_is_none(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "  ")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.google_oauth_client_id is None
    assert settings.google_oauth_client_secret is None
    assert settings.google_oauth_redirect_uri == "http://localhost:8000/api/v1/studio/oauth/callback"
    assert settings.studio_frontend_url == "http://localhost:3000"
