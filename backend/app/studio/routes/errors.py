"""Map service-layer exceptions to HTTP errors for the Studio routes."""

from typing import NoReturn

from fastapi import HTTPException

from app.services.quota import QuotaExceededError
from app.services.youtube import YouTubeAPIError
from app.studio.services.serpapi import SerpApiBudgetExceeded, SerpApiError, SerpApiNotConfigured


def raise_http(exc: Exception) -> NoReturn:
    """Raise the HTTPException that matches ``exc`` (message becomes ``detail``)."""
    message = str(exc)
    if isinstance(exc, YouTubeAPIError):
        if exc.status_code == 404:
            raise HTTPException(status_code=404, detail=message) from None
        if exc.status_code == 403 or exc.status_code >= 500 or exc.status_code == 0:
            raise HTTPException(status_code=502, detail=message) from None
        raise HTTPException(status_code=400, detail=message) from None
    if isinstance(exc, QuotaExceededError):
        raise HTTPException(status_code=429, detail=message) from None
    if isinstance(exc, SerpApiNotConfigured):
        raise HTTPException(status_code=409, detail=message) from None
    if isinstance(exc, SerpApiBudgetExceeded):
        raise HTTPException(status_code=429, detail=message) from None
    if isinstance(exc, SerpApiError):
        raise HTTPException(status_code=502, detail=message) from None
    if isinstance(exc, ValueError):  # includes the URL resolver's errors
        raise HTTPException(status_code=422, detail=message) from None
    raise exc
