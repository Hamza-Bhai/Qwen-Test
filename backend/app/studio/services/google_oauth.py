"""Google OAuth (code flow) for the owner's own channels, plus an authorised HTTP session.

Implemented directly with ``httpx`` (no new dependencies). Each connected channel
keeps its own refresh token; access tokens live only in memory. Tokens are never
logged or put into error messages.
"""

import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.studio.models import StudioGoogleConnection, StudioSetting

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
SCOPES = "https://www.googleapis.com/auth/yt-analytics.readonly https://www.googleapis.com/auth/youtube.readonly"
STATE_KEY = "oauth_state"
STATE_TTL = timedelta(minutes=10)
TIMEOUT = 30.0
RECONNECT_MESSAGE = "Google access was revoked or expired — connect this channel again."


class OAuthNotConfigured(RuntimeError):
    pass


class OAuthStateError(ValueError):
    pass


class GoogleAuthError(RuntimeError):
    """The refresh token no longer works; the owner has to connect the channel again."""


class GoogleApiError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _client_credentials() -> tuple[str, str]:
    settings = get_settings()
    if not settings.google_oauth_client_id or not settings.google_oauth_client_secret:
        raise OAuthNotConfigured(
            "Google OAuth is not configured: set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET in backend/.env."
        )
    return settings.google_oauth_client_id, settings.google_oauth_client_secret


def _google_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"Google returned HTTP {response.status_code}."
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        return error.get("message") or f"Google returned HTTP {response.status_code}."
    if isinstance(error, str):
        return body.get("error_description") or error
    return f"Google returned HTTP {response.status_code}."


def build_auth_url(db: Session) -> str:
    client_id, _ = _client_credentials()
    state = secrets.token_urlsafe(24)
    value = {"value": state, "expires_at": (_now() + STATE_TTL).isoformat()}
    row = db.get(StudioSetting, STATE_KEY)
    if row is None:
        db.add(StudioSetting(key=STATE_KEY, value=value))
    else:
        row.value = value
    db.commit()
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id,
        "redirect_uri": get_settings().google_oauth_redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    })


def _consume_state(db: Session, state: str) -> None:
    row = db.get(StudioSetting, STATE_KEY)
    saved = row.value if row is not None and isinstance(row.value, dict) else None
    if row is not None:
        db.delete(row)  # single use, whatever the outcome
        db.commit()
    if not saved or not state or not secrets.compare_digest(str(saved.get("value")), state):
        raise OAuthStateError("The sign-in link was not started from this app (or was already used). Try Connect again.")
    if datetime.fromisoformat(saved["expires_at"]) < _now():
        raise OAuthStateError("The sign-in took too long and expired. Try Connect again.")


def complete_oauth(db: Session, code: str, state: str, *, http: httpx.Client | None = None) -> StudioGoogleConnection:
    _consume_state(db, state)
    client_id, client_secret = _client_credentials()
    http = http or httpx.Client(timeout=TIMEOUT)
    response = http.request("POST", TOKEN_URL, data={
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": get_settings().google_oauth_redirect_uri,
        "grant_type": "authorization_code",
    })
    if response.status_code != 200:
        raise GoogleApiError(response.status_code, _google_message(response))
    tokens = response.json()
    if not tokens.get("refresh_token"):
        raise GoogleApiError(400, "Google did not return a refresh token. Remove the app's access in your Google account and connect again.")

    channels = http.request(
        "GET", CHANNELS_URL, params={"part": "snippet", "mine": "true"},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    if channels.status_code != 200:
        raise GoogleApiError(channels.status_code, _google_message(channels))
    items = channels.json().get("items") or []
    if not items:
        raise ValueError("This Google account has no YouTube channel.")
    item = items[0]
    snippet = item.get("snippet") or {}
    thumbs = snippet.get("thumbnails") or {}
    thumb = (thumbs.get("medium") or thumbs.get("default") or {}).get("url")

    conn = db.scalars(
        select(StudioGoogleConnection).where(StudioGoogleConnection.youtube_channel_id == item["id"])
    ).first()
    if conn is None:
        conn = StudioGoogleConnection(youtube_channel_id=item["id"], reach_report_ids=[])
        db.add(conn)
    conn.title = snippet.get("title") or item["id"]
    conn.thumbnail_url = thumb
    conn.refresh_token = tokens["refresh_token"]
    conn.scopes = tokens.get("scope") or SCOPES
    conn.status = "connected"
    conn.last_error = None
    conn.connected_at = _now()
    db.commit()
    return conn


class GoogleSession:
    """Authorised calls to Google APIs on behalf of one connected channel."""

    def __init__(self, db: Session, connection: StudioGoogleConnection, *, http: httpx.Client | None = None) -> None:
        self.db = db
        self.connection = connection
        self.http = http or httpx.Client(timeout=TIMEOUT)
        self._access_token: str | None = None

    def _refresh(self) -> str:
        if not self.connection.refresh_token:
            raise GoogleAuthError(RECONNECT_MESSAGE)
        client_id, client_secret = _client_credentials()
        response = self.http.request("POST", TOKEN_URL, data={
            "refresh_token": self.connection.refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
        })
        if response.status_code in (400, 401):
            self.connection.status = "needs_reconnect"
            self.connection.last_error = f"{RECONNECT_MESSAGE} ({_google_message(response)})"
            self.db.commit()
            raise GoogleAuthError(RECONNECT_MESSAGE)
        if response.status_code != 200:
            raise GoogleApiError(response.status_code, _google_message(response))
        self._access_token = response.json()["access_token"]
        return self._access_token

    def _send(self, method: str, url: str, **kwargs) -> httpx.Response:
        token = self._access_token or self._refresh()
        response = self.http.request(method, url, headers={"Authorization": f"Bearer {token}"}, **kwargs)
        if response.status_code == 401:
            token = self._refresh()
            response = self.http.request(method, url, headers={"Authorization": f"Bearer {token}"}, **kwargs)
        if response.status_code >= 400:
            raise GoogleApiError(response.status_code, _google_message(response))
        return response

    def get(self, url: str, params: dict | None = None) -> dict:
        return self._send("GET", url, params=params).json()

    def post(self, url: str, json: dict) -> dict:
        return self._send("POST", url, json=json).json()

    def get_text(self, url: str) -> str:
        return self._send("GET", url).text


def disconnect(db: Session, connection: StudioGoogleConnection, *, http: httpx.Client | None = None) -> None:
    if connection.refresh_token:
        try:
            (http or httpx.Client(timeout=TIMEOUT)).request(
                "POST", REVOKE_URL, data={"token": connection.refresh_token}
            )
        except httpx.HTTPError:
            pass  # the token is dropped locally either way
    connection.refresh_token = None
    connection.status = "disconnected"
    db.commit()
