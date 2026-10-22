"""SQLAlchemy engine / session management.

Sync engine + sessions are used in Phase 1 (simple local setup, easy to test).
Async can be layered on later without changing the models.
"""

import os
import re
from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.base import Base
import app.db.models  # noqa: F401  (registers all tables on Base.metadata)


# Windows drive-letter prefix, e.g. "C:" at the start of a path.
_WIN_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def normalize_sqlite_url(url: str) -> str:
    """Rewrite SQLite/file URLs so they behave identically on Windows and POSIX.

    Four problems this fixes:

    1. *Bare file:// scheme.* ``Path.as_uri()`` produces ``file:///...``,
       which is not a SQLAlchemy dialect (``create_engine`` raises
       NoSuchModuleError). We convert it to the sqlite dialect form. Note
       SQLAlchemy's URL parser puts even absolute file paths in the
       *database* slot WITHOUT a leading slash, so we must re-add it.
    2. *Backslashes / percent-encoding.* On Windows, a configured URL looks
       like ``sqlite:///C:\\Users\\me\\test.db``; SQLAlchemy percent-encodes
       it (``%3A``/``%5C``) when rendering ``engine.url``, breaking naive
       string comparisons used by the test isolation checks.
    3. *Drive letters are not valid POSIX paths.* A Windows-style absolute
       path is silently treated as RELATIVE by SQLite on Linux/macOS —
       pysqlite would create ``./C:/Users/me/test.db`` under cwd instead of
       failing loudly. Converting the drive colon to an underscore keeps
       such a path absolute-but-harmless on POSIX (tests still cannot touch
       the real database). On Windows the drive prefix is left intact —
       there it IS a valid absolute path.
    4. *Relative dev URLs* (``sqlite:///./yt_growth.db``) are anchored to
       the current working directory so they stay stable across chdir.

    The result always uses forward slashes and a leading-slash absolute
    path, e.g. ``sqlite:///C:/Users/me/test.db`` (Windows form) or
    ``sqlite:////var/tmp/x/test.db`` (POSIX form). Non-SQLite/file URLs
    (e.g. plain PostgreSQL) are returned unchanged.
    """
    parsed = make_url(url)
    backend = parsed.get_backend_name()
    if backend not in ("sqlite", "file"):
        return url
    db = parsed.database
    if db is None or db.startswith(":memory:"):
        return url
    # file:///abs/path parses to database == "abs/path" (leading slash lost);
    # restore absoluteness before any other handling.
    if backend == "file" and not _WIN_DRIVE_RE.match(db) and not db.startswith("/"):
        db = "/" + db
    db = db.replace("\\", "/")
    # Canonicalize a Windows drive prefix (C:/...) without letting POSIX
    # os.path.abspath mangle it into a cwd-relative path.
    if _WIN_DRIVE_RE.match(db):
        db = db[0].upper() + ":" + db[2:]
        if os.name != "nt":
            # Not a POSIX absolute path. Neutralize the colon (which would
            # otherwise confuse parsers) and anchor it at filesystem root so
            # it stays absolute and can never collide with a real dev DB.
            db = "/" + db[0] + "_" + db[2:]
    elif not os.path.isabs(db):
        # Keep relative dev URLs stable across cwd changes; make them absolute.
        db = os.path.abspath(db)
    return f"sqlite:///{db}"


def _make_engine(url: str):
    # Normalize SQLite/file URLs up front so create_engine always receives a
    # loadable dialect (bare file:// is not one) and the engine's own URL is
    # already in the canonical cross-platform form.
    url = normalize_sqlite_url(url)
    connect_args = {}
    if make_url(url).get_backend_name() == "sqlite":
        connect_args["check_same_thread"] = False
    eng = create_engine(url, connect_args=connect_args, future=True)
    if eng.url.get_backend_name() == "sqlite":

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


def url_paths_match(a: str, b: str) -> bool:
    """True when two DB URLs refer to the same target, cross-platform.

    For SQLite/file URLs the comparison is done on *normalized* filesystem
    paths: percent-decoded (``%3A``/``%5C``), backslashes converted to
    forward slashes, drive letters canonicalized (``c:\\`` == ``C:/`` ==
    ``file:///c:/``). No ``os.path.realpath`` is used — it resolves against
    the current process's platform semantics and would produce different
    answers on Linux vs Windows; the pure normalization above is identical
    on both. Non-SQLite URLs are compared via their parsed components
    (host/port/database/...), never raw strings.
    """
    ua, ub = make_url(a), make_url(b)
    ba, bb = ua.get_backend_name(), ub.get_backend_name()
    sqliteish = {"sqlite", "file"}
    if not (ba in sqliteish and bb in sqliteish):
        if ba != bb or ua.get_driver_name() != ub.get_driver_name():
            return False
        return ua.render_as_string(hide_password=True) == ub.render_as_string(
            hide_password=True
        )
    da = make_url(normalize_sqlite_url(a)).database
    db_ = make_url(normalize_sqlite_url(b)).database
    if da is None or db_ is None:
        return da == db_  # :memory: style handling
    return da == db_


def rebind_engine(url: str) -> str:
    """Point the module-level engine/session at a new database URL.

    Used by tests (and nothing in production) so the app can follow the
    per-test DATABASE_URL even when this module was imported earlier.
    Disposes the previous engine first so its pooled connections (and any
    SQLite file handles) are released immediately instead of leaking until GC.

    Returns the *normalized* URL the new engine is actually bound to, so
    callers can assert against the real active target instead of the raw
    configured string (which may differ after normalization).
    """
    global engine
    old = engine
    engine = _make_engine(url)
    SessionLocal.configure(bind=engine)
    if old is not engine:
        old.dispose()
    return engine.url.render_as_string(hide_password=False)


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
