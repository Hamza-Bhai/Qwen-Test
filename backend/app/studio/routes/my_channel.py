"""Growth Studio "My channel": Connect with Google, connected channels, pull and overview."""

import logging
from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal, get_db
from app.studio.models import StudioGoogleConnection
from app.studio.services.google_oauth import (
    GoogleApiError,
    GoogleAuthError,
    OAuthNotConfigured,
    build_auth_url,
    complete_oauth,
    disconnect,
)
from app.studio.services.my_channel import connection_brief, get_overview, list_connections, pull_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/studio", tags=["studio"])


def _back_to_ui(**params) -> RedirectResponse:
    query = urlencode({"tab": "my-channel", **params})
    return RedirectResponse(f"{get_settings().studio_frontend_url}/studio?{query}", status_code=307)


def pull_in_background(connection_id: int) -> None:
    """First pull right after connecting (own session; never raises)."""
    db = SessionLocal()
    try:
        connection = db.get(StudioGoogleConnection, connection_id)
        if connection is not None:
            pull_connection(db, connection)
    except Exception as exc:  # noqa: BLE001
        logger.warning("First pull after connect failed for %s: %s", connection_id, exc)
    finally:
        db.close()


@router.get("/oauth/start")
def oauth_start(db: Session = Depends(get_db)):
    try:
        return RedirectResponse(build_auth_url(db), status_code=307)
    except OAuthNotConfigured as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get("/oauth/callback")
def oauth_callback(
    background: BackgroundTasks,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    if error:
        return _back_to_ui(oauth_error=f"Google sign-in was cancelled or refused ({error}).")
    try:
        connection = complete_oauth(db, code or "", state or "")
    except (ValueError, OAuthNotConfigured, GoogleApiError) as exc:  # OAuthStateError is a ValueError
        db.rollback()
        return _back_to_ui(oauth_error=str(exc))
    background.add_task(pull_in_background, connection.id)
    return _back_to_ui(connected=connection.id)


def _get(db: Session, connection_id: int) -> StudioGoogleConnection:
    connection = db.get(StudioGoogleConnection, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="That channel is not connected.")
    return connection


@router.get("/my-channels")
def read_my_channels(db: Session = Depends(get_db)) -> list[dict]:
    return [connection_brief(c) for c in list_connections(db)]


@router.post("/my-channels/{connection_id}/pull")
def pull_my_channel(connection_id: int, db: Session = Depends(get_db)) -> dict:
    connection = _get(db, connection_id)
    if connection.status != "connected":
        raise HTTPException(status_code=409, detail="This channel needs to be connected again before pulling.")
    try:
        return pull_connection(db, connection)
    except GoogleAuthError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except GoogleApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None


@router.get("/my-channels/{connection_id}/overview")
def my_channel_overview(connection_id: int, db: Session = Depends(get_db)) -> dict:
    return get_overview(db, _get(db, connection_id))


@router.delete("/my-channels/{connection_id}", status_code=204)
def disconnect_my_channel(connection_id: int, db: Session = Depends(get_db)) -> Response:
    disconnect(db, _get(db, connection_id))
    return Response(status_code=204)
