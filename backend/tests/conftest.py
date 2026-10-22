"""Shared pytest fixtures: isolated SQLite DB + fake YouTube API responses.

Tests never hit the real Google API. The YouTube service talks to Google
through ``httpx.Client.get``, so we patch that method with a recorder that
returns realistic channels.list / playlistItems.list / videos.list payloads
matching actual API response shapes, then assert the values returned by our
API match the API response exactly — nothing invented, nothing dropped.

Two isolation rules encoded here
--------------------------------
1. *HTTP mock scoping.* Starlette's ``TestClient`` is itself built on
   ``httpx.Client``, so an unscoped patch of ``httpx.Client.get`` also
   intercepts in-process requests to our own FastAPI app (they arrive as
   ``http://testserver/...``). Every non-Google call previously fell through
   to a canned 404, producing phantom failures like ``KeyError['units_used']``
   and ``assert 404 == 200`` on perfectly good endpoints. The fake now routes
   strictly by hostname: only ``www.googleapis.com`` URLs are faked; anything
   else is delegated to the original ``httpx.Client.get`` so TestClient keeps
   working untouched.

2. *Database isolation.* ``app.db.session`` creates its module-level engine
   at IMPORT time from ``get_settings().database_url``. Test modules import
   ``app.main`` (which imports the session module) at collection time — long
   before any per-test fixture runs. So we bind ONCE, eagerly, when this
   conftest is imported (guaranteed before any test module import), and each
   test additionally rebinds to its own ``tmp_path`` SQLite file, disposes the
   superseded engine, and asserts the active engine URL matches the expected
   temp file. Tests can therefore never read or write the developer's real
   ``backend/yt_growth.db``.
"""

import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Eager binding: runs at conftest import, i.e. BEFORE test modules are
# imported and before app.db.session builds its initial engine.
# ---------------------------------------------------------------------------
_TMP_DIR = Path(tempfile.mkdtemp(prefix="yt-growth-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DIR / 'session.db'}"
os.environ.setdefault("YOUTUBE_API_KEY", "test-api-key")
os.environ.setdefault("ENABLE_SCHEDULER", "false")

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()  # pick up the env vars above before first use
SETTINGS = get_settings()
TEST_DB_URL = SETTINGS.database_url

from app.db import session as _db_session  # noqa: E402

_db_session.rebind_engine(TEST_DB_URL)


import httpx  # noqa: E402
import pytest  # noqa: E402

# Captured once at import time, while monkeypatching has not yet begun, so
# the hostname-routing fake can always delegate non-Google calls back to the
# genuine implementation (this is what makes TestClient keep working).
_ORIGINAL_HTTPX_GET = httpx.Client.get

GOOGLE_APIS_HOSTS = {"www.googleapis.com", "googleapis.com"}


def _is_google_api(url) -> bool:
    host = urlparse(str(url)).hostname or ""
    return host in GOOGLE_APIS_HOSTS or host.endswith(".googleapis.com")


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Per-test DATABASE_URL + dummy key, applied BEFORE app code reads them.

    ``chdir`` keeps the app's relative ``sqlite:///./...`` default (and any
    stray .env discovery) away from the developer's real database. We clear
    the settings cache and rebind the engine so each test gets a fresh,
    absolute-path SQLite file in its own ``tmp_path``.
    """
    db_path = tmp_path / "test.db"
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    # Plain absolute path in the sqlite:/// form. _make_engine normalizes it
    # cross-platform (backslash/percent-encoding/drive-letter handling), and
    # the assertion below compares NORMALIZED paths, not raw strings.
    configured_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", configured_url)
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-api-key")
    monkeypatch.setenv("ENABLE_SCHEDULER", "false")
    # Never call youtube.com/shorts/ from tests: "check unavailable" makes the
    # classifier use its duration fallback.
    monkeypatch.setattr("app.services.channel_service.check_is_short", lambda *a, **k: None)

    get_settings.cache_clear()
    url = get_settings().database_url
    active_url = _db_session.rebind_engine(url)

    # Safety net: refuse to run against anything that isn't this test's file.
    # Comparison is done on normalized filesystem paths (SQLAlchemy URL
    # parsing + decoded/separator-canonicalized paths), NOT raw strings —
    # on Windows the raw configured URL uses backslashes while
    # ``engine.url.render_as_string()`` percent-encodes them (%3A/%5C), and
    # a naive == would fail spuriously even though both point at the same
    # temp file. The check itself remains fully enforced.
    assert _db_session.url_paths_match(active_url, url), (
        f"engine isolation failed: bound to {active_url!r}, expected {url!r}"
    )
    # Belt and braces: the normalized SQLite file must be exactly this
    # test's tmp_path database, and never the developer's real
    # backend/yt_growth.db. Comparison uses the same platform-neutral
    # normalization as url_paths_match (no realpath — its semantics differ
    # between Windows and POSIX for drive-letter paths).
    from sqlalchemy.engine import make_url as _make_url

    bound_db = _make_url(_db_session.normalize_sqlite_url(active_url)).database
    expected_db = _make_url(
        _db_session.normalize_sqlite_url(configured_url)).database
    if bound_db is not None and expected_db is not None:
        assert bound_db == expected_db, (
            f"engine points at {bound_db!r}, expected {expected_db!r}"
        )
        dev_db = _make_url(
            _db_session.normalize_sqlite_url(
                "sqlite:///" + str(
                    Path(__file__).resolve().parent.parent / "yt_growth.db")
            )
        ).database
        assert bound_db != dev_db, "test engine would touch the development DB!"

    yield

    get_settings.cache_clear()


# --------------------------------------------------------------------- payloads
CHANNEL_ITEM = {
    "kind": "youtube#channel",
    "etag": "abc",
    "id": "UC8-Th83bH_thdKZDJCrn88g",
    "snippet": {
        "title": "MKBHD",
        "description": "Tech reviews and more.",
        "publishedAt": "2010-07-03T01:09:50Z",
        "country": "US",
        "thumbnails": {
            "default": {"url": "https://img.example/default.jpg"},
            "high": {"url": "https://img.example/high.jpg"},
        },
    },
    "statistics": {
        "viewCount": "5000000000",
        "videoCount": "900",
        "subscriberCount": "40000000",
        "hiddenSubscriberCount": False,
    },
    "contentDetails": {
        "relatedPlaylists": {"uploads": "UU8-Th83bH_thdKZDJCrn88g"}
    },
}

PLAYLIST_ITEMS = {
    "items": [
        {"snippet": {"title": "v1"}, "contentDetails": {"videoId": "AAAAAAAAAA1"}}
    ]
}

VIDEOS_LIST = {
    "items": [
        {
            "id": "AAAAAAAAAA1",
            "snippet": {
                "title": "Cool Tech Video",
                "description": "desc",
                "publishedAt": "2024-01-01T00:00:00Z",
                "categoryId": "28",
                "tags": ["tech"],
                "thumbnails": {"high": {"url": "https://img.example/v1.jpg"}},
            },
            "statistics": {
                "viewCount": "1234567",
                "likeCount": "54321",
                "commentCount": "3210",
            },
            "contentDetails": {"duration": "PT12M34S"},
        }
    ]
}


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class _EmptyMarker:
    """Sentinel type so 'no channel found' is distinguishable from 'unset'."""


EMPTY = _EmptyMarker()
_SENTINEL = object()


def make_fake_get(monkeypatch, *, channel_item=_SENTINEL, error_status=None, error_message=None):
    """Replace httpx.Client.get with a recorder that returns canned payloads.

    ``channel_item`` semantics: the default (sentinel) means "serve the
    standard CHANNEL_ITEM". Pass ``EMPTY`` to simulate Google returning an
    empty items list for channels.list (the real API's not-found shape).
    ``None`` is kept as a legacy alias of EMPTY.

    The fake also *invalidates* the app's DB-backed response cache on install.
    Rationale: YouTubeService caches channels.list payloads for
    ``cache_ttl_seconds``; without invalidation, a test that installs a new
    fake after an earlier request already populated the cache would receive
    the stale cached payload instead of its own — producing phantom failures
    like "not found" tests getting 201. Invalidating between tests mirrors
    what a fresh process would see in production and keeps each test's mock
    authoritative. No production code changes are involved.
    """
    calls = []

    if channel_item is _SENTINEL:
        item = CHANNEL_ITEM
    elif channel_item is None:  # legacy alias for EMPTY
        item = EMPTY
    else:
        item = channel_item

    _invalidate_youtube_cache()

    def fake_get(self, url, params=None, **kwargs):  # noqa: ANN001
        # Scope the mock strictly to outgoing Google API traffic. Anything
        # else (notably Starlette TestClient's in-process requests against
        # http://testserver) must reach the real implementation untouched.
        if not _is_google_api(url):
            return _ORIGINAL_HTTPX_GET(self, url, params=params, **kwargs)
        p = dict(params or {})
        calls.append({"url": url, "params": p})
        if error_status is not None:
            return FakeResponse(
                error_status,
                {"error": {"code": error_status, "message": error_message or "fake error"}},
            )
        path = urlparse(str(url)).path
        if "/channels" in path:
            if isinstance(item, _EmptyMarker):
                return FakeResponse(200, {"items": []})
            return FakeResponse(200, {"items": [item]})
        if "/playlistItems" in path:
            return FakeResponse(200, PLAYLIST_ITEMS)
        if "/videos" in path:
            return FakeResponse(200, VIDEOS_LIST)
        return FakeResponse(404, {"error": {"message": "unexpected endpoint"}})

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    return calls


def _invalidate_youtube_cache() -> None:
    """Drop all rows in the app's api_cache table for the current test engine.

    Best-effort: silently no-ops when no usable session exists (e.g. helper
    used outside a DB-bound test), so it can never break non-cache tests.
    """
    try:
        from sqlalchemy import delete

        from app.db.models import ApiCache
        from app.db.session import SessionLocal

        db = SessionLocal()
        try:
            db.execute(delete(ApiCache).where(ApiCache.service == "youtube"))
            db.commit()
        finally:
            db.close()
    except Exception:
        pass
