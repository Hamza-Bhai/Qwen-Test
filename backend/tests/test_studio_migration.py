"""studio_setting table: ORM roundtrip and the Alembic migration."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from app.db.session import SessionLocal, init_db

BACKEND_DIR = Path(__file__).resolve().parent.parent


def test_studio_setting_roundtrip():
    from app.studio.models import StudioSetting

    init_db()
    with SessionLocal() as db:
        db.add(StudioSetting(key="k", value=["a", "b"]))
        db.commit()
    with SessionLocal() as db:
        row = db.get(StudioSetting, "k")
        assert row is not None
        assert row.value == ["a", "b"]
        assert row.updated_at is not None


def test_alembic_upgrade_head_creates_studio_setting(tmp_path):
    db_file = tmp_path / "mig.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_file}"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    con = sqlite3.connect(db_file)
    try:
        tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        version = con.execute("select version_num from alembic_version").fetchone()[0]
    finally:
        con.close()
    assert "studio_setting" in tables
    assert version == "c9d1e2f3a4b5"


def test_upgrade_is_safe_when_create_all_already_made_the_table(tmp_path):
    """The dev backend runs create_all at startup; a later `alembic upgrade head`
    on a database stamped at the previous revision must not fail on the table."""
    db_file = tmp_path / "pre.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_file}"}

    def alembic(*args):
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=BACKEND_DIR, env=env, capture_output=True, text=True,
        )

    assert alembic("upgrade", "b8c4d0f3e6a2").returncode == 0
    create_all = subprocess.run(
        [sys.executable, "-c", "from app.db.session import init_db; init_db()"],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True,
    )
    assert create_all.returncode == 0, create_all.stderr

    result = alembic("upgrade", "head")
    assert result.returncode == 0, result.stderr
