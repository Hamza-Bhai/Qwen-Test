"""My-channel API: OAuth start/callback, list, pull, overview, disconnect."""

from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.session import SessionLocal, init_db
from app.studio.models import StudioChannelDaily, StudioGoogleConnection
from app.studio.services import my_channel
from app.studio.services.google_oauth import GoogleApiError, GoogleAuthError, OAuthStateError
from tests.studio_fakes import FakeSession, make_connection

API = "/api/v1/studio"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csecret")
    get_settings.cache_clear()
    from app.main import app

    init_db()
    with TestClient(app, follow_redirects=False) as c:
        yield c


def _conn(**kw):
    with SessionLocal() as db:
        return make_connection(db, **kw).id


def test_start_redirects_to_google(client):
    r = client.get(f"{API}/oauth/start")
    assert r.status_code == 307
    assert r.headers["location"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")


def test_start_409_without_client(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "")
    get_settings.cache_clear()
    r = client.get(f"{API}/oauth/start")
    assert r.status_code == 409 and "GOOGLE_OAUTH_CLIENT_ID" in r.json()["detail"]


def test_callback_success_redirects_with_connected_id(client, monkeypatch):
    pulled = []

    def fake_complete(db, code, state, **kw):
        assert (code, state) == ("c1", "s1")
        return make_connection(db)

    monkeypatch.setattr("app.studio.routes.my_channel.complete_oauth", fake_complete)
    monkeypatch.setattr("app.studio.routes.my_channel.pull_in_background", lambda conn_id: pulled.append(conn_id))
    r = client.get(f"{API}/oauth/callback", params={"code": "c1", "state": "s1"})
    assert r.status_code == 307
    loc = urlparse(r.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == "http://localhost:3000/studio"
    q = parse_qs(loc.query)
    assert q["tab"] == ["my-channel"] and q["connected"] == [str(pulled[0])]


def test_callback_error_param_redirects_with_message(client):
    r = client.get(f"{API}/oauth/callback", params={"error": "access_denied"})
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert r.status_code == 307 and "access_denied" in q["oauth_error"][0]


def test_callback_bad_state_redirects_with_message(client, monkeypatch):
    def boom(*a, **k):
        raise OAuthStateError("The sign-in took too long and expired. Try Connect again.")

    monkeypatch.setattr("app.studio.routes.my_channel.complete_oauth", boom)
    r = client.get(f"{API}/oauth/callback", params={"code": "c", "state": "s"})
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["oauth_error"] == ["The sign-in took too long and expired. Try Connect again."]
    with SessionLocal() as db:
        assert db.query(StudioGoogleConnection).count() == 0


def test_list_never_contains_refresh_token(client):
    _conn()
    r = client.get(f"{API}/my-channels")
    assert r.status_code == 200
    assert r.json()[0]["youtube_channel_id"] == "UCmine1" and r.json()[0]["status"] == "connected"
    assert "rt" not in r.text.replace("start", "")  # the token value "rt" never appears as a value
    assert "refresh_token" not in r.text


def test_pull_404_for_unknown(client):
    assert client.post(f"{API}/my-channels/999/pull").status_code == 404


def test_pull_409_when_needs_reconnect(client):
    conn_id = _conn(status="needs_reconnect")
    r = client.post(f"{API}/my-channels/{conn_id}/pull")
    assert r.status_code == 409 and "connect" in r.json()["detail"].lower()


def test_pull_409_when_token_revoked_during_pull(client, monkeypatch):
    conn_id = _conn()

    def revoked(db, conn, **kw):
        raise GoogleAuthError("Google access was revoked or expired — connect this channel again.")

    monkeypatch.setattr("app.studio.routes.my_channel.pull_connection", revoked)
    assert client.post(f"{API}/my-channels/{conn_id}/pull").status_code == 409


def test_pull_502_on_google_api_error(client, monkeypatch):
    conn_id = _conn()

    def failing(db, conn, **kw):
        raise GoogleApiError(500, "backend error")

    monkeypatch.setattr("app.studio.routes.my_channel.pull_connection", failing)
    assert client.post(f"{API}/my-channels/{conn_id}/pull").status_code == 502


def _stub_pull_steps(monkeypatch, reach_error=None):
    monkeypatch.setattr(my_channel, "sync_my_videos", lambda db, conn, s: 2)
    monkeypatch.setattr(my_channel, "pull_channel_daily", lambda db, conn, s, a, b: 5)
    monkeypatch.setattr(my_channel, "pull_traffic", lambda db, conn, s, a, b: 6)
    monkeypatch.setattr(my_channel, "pull_video_totals", lambda db, conn, s, t: 2)
    monkeypatch.setattr(my_channel, "pull_retention", lambda db, conn, s, t: 1)
    monkeypatch.setattr(my_channel, "ensure_reach_job", lambda db, conn, s: "j1")

    def reach(db, conn, s):
        if reach_error:
            raise reach_error
        return {"files": 1, "rows": 3}

    monkeypatch.setattr(my_channel, "pull_reach", reach)


def test_pull_connection_summary_and_timestamps(monkeypatch):
    _stub_pull_steps(monkeypatch)
    init_db()
    with SessionLocal() as db:
        conn = make_connection(db, last_error="old")
        result = my_channel.pull_connection(db, conn, session=FakeSession(), today=date(2026, 10, 24))
        assert result["videos"] == 2 and result["channel_days"] == 5 and result["reach"] == {"files": 1, "rows": 3}
        assert conn.last_pulled_at is not None and conn.last_error is None


def test_pull_reach_failure_does_not_fail_pull(monkeypatch):
    _stub_pull_steps(monkeypatch, reach_error=GoogleApiError(403, "Reporting API disabled"))
    init_db()
    with SessionLocal() as db:
        conn = make_connection(db)
        result = my_channel.pull_connection(db, conn, session=FakeSession(), today=date(2026, 10, 24))
        assert result["reach_error"] == "Reporting API disabled"
        assert result["channel_days"] == 5 and conn.last_pulled_at is not None


def test_overview_empty_channel_renders(client):
    conn_id = _conn()
    r = client.get(f"{API}/my-channels/{conn_id}/overview")
    assert r.status_code == 200
    body = r.json()
    assert body["channel"]["id"] == conn_id and body["daily"] == [] and body["videos"] == []
    assert body["reach_status"] == "no_job"
    assert len(body["findings"]) == 9 and all(f["status"] == "inconclusive" for f in body["findings"])
    assert body["progress"]["views"]["last_7"] is None


def test_overview_includes_daily_rows(client):
    conn_id = _conn(reach_job_id="j1")
    with SessionLocal() as db:
        db.add(StudioChannelDaily(connection_id=conn_id, day=date.today() - timedelta(days=2), views=7, watch_minutes=1,
                                  avg_view_duration_s=1, avg_view_pct=1.0, subs_gained=0, subs_lost=0,
                                  likes=0, comments=0, shares=0))
        db.commit()
    body = client.get(f"{API}/my-channels/{conn_id}/overview").json()
    assert body["reach_status"] == "waiting_for_first_report"
    assert [d["views"] for d in body["daily"]] == [7]


def test_delete_disconnects(client, monkeypatch):
    conn_id = _conn()
    calls = []
    monkeypatch.setattr("app.studio.routes.my_channel.disconnect", lambda db, conn: calls.append(conn.id))
    assert client.delete(f"{API}/my-channels/{conn_id}").status_code == 204
    assert calls == [conn_id]


def test_callback_network_error_redirects_with_message(client, monkeypatch):
    import httpx

    def offline(*a, **k):
        raise httpx.ConnectError("getaddrinfo failed")

    monkeypatch.setattr("app.studio.routes.my_channel.complete_oauth", offline)
    r = client.get(f"{API}/oauth/callback", params={"code": "c", "state": "s"})
    assert r.status_code == 307
    assert "reach Google" in parse_qs(urlparse(r.headers["location"]).query)["oauth_error"][0]


def test_pull_network_error_is_502(client, monkeypatch):
    import httpx

    conn_id = _conn()

    def offline(db, conn, **kw):
        raise httpx.ConnectError("getaddrinfo failed")

    monkeypatch.setattr("app.studio.routes.my_channel.pull_connection", offline)
    r = client.post(f"{API}/my-channels/{conn_id}/pull")
    assert r.status_code == 502 and "reach Google" in r.json()["detail"]
