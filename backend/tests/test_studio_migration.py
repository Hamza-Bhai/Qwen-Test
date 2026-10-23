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
