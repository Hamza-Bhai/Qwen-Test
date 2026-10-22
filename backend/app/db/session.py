"""SQLAlchemy engine / session management.

Sync engine + sessions are used in Phase 1 (simple local setup, easy to test).
Async can be layered on later without changing the models.
"""

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.base import Base
import app.db.models  # noqa: F401  (registers all tables on Base.metadata)

def _make_engine(url: str):
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    eng = create_engine(url, connect_args=connect_args, future=True)
    if url.startswith("sqlite"):

        @event.listens_for(eng, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ANN001
            """Enable WAL and foreign keys on SQLite (parity with PostgreSQL FKs)."""
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return eng


engine = _make_engine(get_settings().database_url)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_engine():
    """Return the CURRENT module-level engine.

    Always resolve the engine through this function (never ``from
    app.db.session import engine``) so test isolation via ``rebind_engine``
    cannot be defeated by an early-imported stale reference.
    """
    return engine


def rebind_engine(url: str):
    """Point the module-level engine/session at a new database URL.

    Used by tests (and nothing in production) so the app can follow the
    per-test DATABASE_URL even when this module was imported earlier.
    Disposes the previous engine first so its pooled connections (and any
    SQLite file handles) are released immediately instead of leaking until GC.
    Returns the previous engine so callers can restore it.
    """
    global engine
    old = engine
    engine = _make_engine(url)
    SessionLocal.configure(bind=engine)
    if old is not engine:
        old.dispose()
    return old


def init_db() -> None:
    """Create tables if they do not exist (convenience for local dev).

    Schema changes for committed code should go through Alembic migrations.
    """
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a scoped database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
