"""Google OAuth for own channels: auth URL, code exchange, token refresh, disconnect."""

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.core.config import get_settings
from app.db.session import SessionLocal, init_db
from app.studio.models import StudioGoogleConnection, StudioSetting
from app.studio.services.google_oauth import (
    GoogleApiError,
    GoogleAuthError,
    GoogleSession,
    OAuthNotConfigured,
    OAuthStateError,
    build_auth_url,
    complete_oauth,
    disconnect,
)

CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csecret")
    get_settings.cache_clear()
    init_db()
    with SessionLocal() as session:
        yield session


class FakeGoogle:
    """MockTransport handler: token endpoint, channels.list mine, revoke, and a generic API url."""

    def __init__(self, *, channels=None, token_status=200, token_body=None, api_responses=None):
        self.requests: list[httpx.Request] = []
        self.channels = [{"id": "UCmine", "snippet": {"title": "AXIOM", "thumbnails": {"default": {"url": "t.jpg"}}}}] \
            if channels is None else channels
        self.token_status = token_status
        self.token_body = token_body
        self.api_responses = list(api_responses or [])
        self.token_calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url.startswith("https://oauth2.googleapis.com/token"):
            self.token_calls += 1
            body = self.token_body or {
                "access_token": f"at-{self.token_calls}", "expires_in": 3599,
                "refresh_token": "rt-new", "scope": "s1 s2",
            }
            return httpx.Response(self.token_status, json=body)
        if url.startswith("https://oauth2.googleapis.com/revoke"):
            return httpx.Response(200, json={})
        if url.startswith(CHANNELS_URL):
            return httpx.Response(200, json={"items": self.channels})
        status, body = self.api_responses.pop(0)
        return httpx.Response(status, json=body)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def _state(db) -> str:
    url = build_auth_url(db)
    return parse_qs(urlparse(url).query)["state"][0]


def _connection(db, **kw) -> StudioGoogleConnection:
    conn = StudioGoogleConnection(
        youtube_channel_id="UCmine", title="AXIOM", refresh_token="rt-old", scopes="s", status="connected", **kw
    )
    db.add(conn)
    db.commit()
    return conn


def test_auth_url_has_scopes_offline_consent_and_state(db):
    url = build_auth_url(db)
    q = parse_qs(urlparse(url).query)
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert q["client_id"] == ["cid"]
    assert q["redirect_uri"] == ["http://localhost:8000/api/v1/studio/oauth/callback"]
    assert q["scope"] == [
        "https://www.googleapis.com/auth/yt-analytics.readonly https://www.googleapis.com/auth/youtube.readonly"
    ]
    assert q["access_type"] == ["offline"] and q["prompt"] == ["consent"] and q["response_type"] == ["code"]
    assert db.get(StudioSetting, "oauth_state").value["value"] == q["state"][0]


def test_auth_url_without_client_raises(db, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "")
    get_settings.cache_clear()
    with pytest.raises(OAuthNotConfigured):
        build_auth_url(db)


def test_complete_creates_connection_and_consumes_state(db):
    fake = FakeGoogle()
    conn = complete_oauth(db, "code-1", _state(db), http=fake.client())
    assert conn.youtube_channel_id == "UCmine" and conn.title == "AXIOM" and conn.thumbnail_url == "t.jpg"
    assert conn.refresh_token == "rt-new" and conn.status == "connected" and conn.scopes == "s1 s2"
    token_req = fake.requests[0]
    form = parse_qs(token_req.content.decode())
    assert form["code"] == ["code-1"] and form["grant_type"] == ["authorization_code"]
    assert fake.requests[1].headers["Authorization"] == "Bearer at-1"
    assert db.get(StudioSetting, "oauth_state") is None or db.get(StudioSetting, "oauth_state").value is None


def test_complete_wrong_state_raises_and_stores_nothing(db):
    _state(db)
    fake = FakeGoogle()
    with pytest.raises(OAuthStateError):
        complete_oauth(db, "code", "not-the-state", http=fake.client())
    assert fake.requests == []
    assert db.query(StudioGoogleConnection).count() == 0


def test_complete_expired_state_raises(db):
    state = _state(db)
    row = db.get(StudioSetting, "oauth_state")
    row.value = {**row.value, "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}
    db.commit()
    with pytest.raises(OAuthStateError):
        complete_oauth(db, "code", state, http=FakeGoogle().client())


def test_complete_account_without_channel_raises(db):
    with pytest.raises(ValueError, match="no YouTube channel"):
        complete_oauth(db, "code", _state(db), http=FakeGoogle(channels=[]).client())
    assert db.query(StudioGoogleConnection).count() == 0


def test_reconnect_same_channel_updates_one_row(db):
    old = _connection(db, last_error="boom")
    old.status = "needs_reconnect"
    db.commit()
    conn = complete_oauth(db, "code", _state(db), http=FakeGoogle().client())
    assert conn.id == old.id
    assert db.query(StudioGoogleConnection).count() == 1
    assert conn.refresh_token == "rt-new" and conn.status == "connected" and conn.last_error is None


def test_session_refreshes_once_and_reuses_token(db):
    conn = _connection(db)
    fake = FakeGoogle(api_responses=[(200, {"a": 1}), (200, {"a": 2})])
    session = GoogleSession(db, conn, http=fake.client())
    assert session.get("https://youtubeanalytics.googleapis.com/v2/reports", {"x": "1"}) == {"a": 1}
    assert session.get("https://youtubeanalytics.googleapis.com/v2/reports") == {"a": 2}
    assert fake.token_calls == 1
    form = parse_qs(fake.requests[0].content.decode())
    assert form["grant_type"] == ["refresh_token"] and form["refresh_token"] == ["rt-old"]
    assert fake.requests[1].url.params["x"] == "1"


def test_session_retries_once_on_401(db):
    conn = _connection(db)
    fake = FakeGoogle(api_responses=[(401, {"error": {"message": "expired"}}), (200, {"ok": True})])
    assert GoogleSession(db, conn, http=fake.client()).get("https://x.googleapis.com/a") == {"ok": True}
    assert fake.token_calls == 2


def test_invalid_grant_marks_needs_reconnect(db):
    conn = _connection(db)
    fake = FakeGoogle(token_status=400, token_body={"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
    with pytest.raises(GoogleAuthError, match="connect this channel again"):
        GoogleSession(db, conn, http=fake.client()).get("https://x.googleapis.com/a")
    db.refresh(conn)
    assert conn.status == "needs_reconnect" and conn.last_error


def test_session_without_refresh_token_raises(db):
    conn = _connection(db)
    conn.refresh_token = None
    db.commit()
    with pytest.raises(GoogleAuthError):
        GoogleSession(db, conn, http=FakeGoogle().client()).get("https://x.googleapis.com/a")


def test_api_error_carries_status_and_message(db):
    conn = _connection(db)
    fake = FakeGoogle(api_responses=[(403, {"error": {"code": 403, "message": "Forbidden thing"}})])
    with pytest.raises(GoogleApiError) as info:
        GoogleSession(db, conn, http=fake.client()).get("https://x.googleapis.com/a")
    assert info.value.status_code == 403 and "Forbidden thing" in str(info.value)


def test_post_sends_json(db):
    conn = _connection(db)
    fake = FakeGoogle(api_responses=[(200, {"id": "job1"})])
    assert GoogleSession(db, conn, http=fake.client()).post("https://x.googleapis.com/jobs", {"a": 1}) == {"id": "job1"}
    assert json.loads(fake.requests[-1].content) == {"a": 1}


def test_disconnect_revokes_and_clears_token(db):
    conn = _connection(db)
    fake = FakeGoogle()
    disconnect(db, conn, http=fake.client())
    db.refresh(conn)
    assert conn.refresh_token is None and conn.status == "disconnected"
    assert str(fake.requests[0].url).startswith("https://oauth2.googleapis.com/revoke")


def test_refresh_token_never_in_repr(db):
    conn = _connection(db)
    assert "rt-old" not in repr(conn)
    fake = FakeGoogle(token_status=400, token_body={"error": "invalid_grant"})
    with pytest.raises(GoogleAuthError) as info:
        GoogleSession(db, conn, http=fake.client()).get("https://x.googleapis.com/a")
    assert "rt-old" not in str(info.value)
