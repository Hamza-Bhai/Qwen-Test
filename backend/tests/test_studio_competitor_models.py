"""Competitor tables: ORM behaviour and the Alembic migration."""

import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.session import SessionLocal, init_db
from app.studio.models import (
    StudioCompetitor,
    StudioCompetitorVideo,
    StudioCompetitorVideoSnapshot,
)

BACKEND_DIR = Path(__file__).resolve().parent.parent
HEAD = "d2e3f4a5b6c7"
NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


@pytest.fixture()
def db():
    init_db()
    with SessionLocal() as session:
        yield session


def _competitor(n=1, **kw):
    return StudioCompetitor(
        youtube_channel_id=f"UC{n:022d}", title=f"Comp {n}", status="watching", source="manual", **kw
    )


def test_competitor_roundtrip_with_videos_and_snapshots(db):
    comp = _competitor(1, subscriber_count=None, notes="calm niche")
    vid = StudioCompetitorVideo(
        youtube_video_id="aaaaaaaaaaa", title="Sunday reset", view_count=10,
        tags=["a"], hashtags=["b"], first_seen_at=NOW, last_synced_at=NOW,
    )
    vid.snapshots.append(StudioCompetitorVideoSnapshot(captured_at=NOW, view_count=10))
    comp.videos.append(vid)
    db.add(comp)
    db.commit()

    again = db.get(StudioCompetitor, comp.id)
    assert again.subscriber_count is None and again.notes == "calm niche"
    assert again.added_at is not None and again.status_changed_at is not None
    assert again.videos[0].youtube_video_id == "aaaaaaaaaaa"
    assert again.videos[0].tags == ["a"] and again.videos[0].hashtags == ["b"]
    assert again.videos[0].snapshots[0].view_count == 10


def test_deleting_competitor_cascades_to_videos_and_snapshots(db):
    comp = _competitor(1)
    vid = StudioCompetitorVideo(youtube_video_id="bbbbbbbbbbb", title="t", first_seen_at=NOW, last_synced_at=NOW)
    vid.snapshots.append(StudioCompetitorVideoSnapshot(captured_at=NOW, view_count=1))
    comp.videos.append(vid)
    db.add(comp)
    db.commit()
    db.delete(comp)
    db.commit()
    assert db.query(StudioCompetitorVideo).count() == 0
    assert db.query(StudioCompetitorVideoSnapshot).count() == 0


def test_youtube_channel_id_is_unique(db):
    db.add(_competitor(1))
    db.commit()
    db.add(_competitor(1))
    with pytest.raises(IntegrityError):
        db.commit()


def _alembic(env, *args):
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=BACKEND_DIR, env=env, capture_output=True, text=True
    )


def test_alembic_head_creates_competitor_tables(tmp_path):
    db_file = tmp_path / "mig.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_file}"}
    result = _alembic(env, "upgrade", HEAD)  # pinned: head moved on in milestone C
    assert result.returncode == 0, result.stderr
    con = sqlite3.connect(db_file)
    try:
        tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        version = con.execute("select version_num from alembic_version").fetchone()[0]
    finally:
        con.close()
    assert {"studio_competitor", "studio_competitor_video", "studio_competitor_video_snapshot"} <= tables
    assert version == HEAD


def test_competitor_migration_safe_after_create_all(tmp_path):
    db_file = tmp_path / "pre.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_file}"}
    assert _alembic(env, "upgrade", "c9d1e2f3a4b5").returncode == 0
    create_all = subprocess.run(
        [sys.executable, "-c", "from app.db.session import init_db; init_db()"],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True,
    )
    assert create_all.returncode == 0, create_all.stderr
    result = _alembic(env, "upgrade", "head")
    assert result.returncode == 0, result.stderr
