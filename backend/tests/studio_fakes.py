"""Test doubles shared by the my-channel tests (no network)."""

from app.db.session import SessionLocal, init_db
from app.studio.models import StudioGoogleConnection


class FakeSession:
    """Duck-typed ``GoogleSession``: answers by URL prefix (+ optional param match), records calls."""

    def __init__(self, routes=None, texts=None):
        self.routes = list(routes or [])  # (url_prefix, params_subset | None, response | callable)
        self.texts = dict(texts or {})
        self.calls: list[tuple[str, str, dict]] = []

    def _answer(self, method, url, params):
        self.calls.append((method, url, dict(params or {})))
        for prefix, match, response in self.routes:
            if url.startswith(prefix) and all(str((params or {}).get(k)) == str(v) for k, v in (match or {}).items()):
                return response(params) if callable(response) else response
        raise AssertionError(f"unexpected {method} {url} {params}")

    def get(self, url, params=None):
        return self._answer("GET", url, params)

    def post(self, url, json):
        return self._answer("POST", url, json)

    def get_text(self, url):
        self.calls.append(("GET", url, {}))
        return self.texts[url]


def make_connection(db, n=1, **kw) -> StudioGoogleConnection:
    conn = StudioGoogleConnection(
        youtube_channel_id=f"UCmine{n}", title=f"Mine {n}", refresh_token="rt", scopes="s",
        status=kw.pop("status", "connected"), reach_report_ids=[], **kw
    )
    db.add(conn)
    db.commit()
    return conn


def open_db():
    init_db()
    return SessionLocal()
