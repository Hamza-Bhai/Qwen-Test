"""SerpApi client for Growth Studio (discovery, trends, transcripts).

Every successful live search costs one of the owner's monthly searches, so the
client enforces a hard monthly cap, caches responses (repeats are free) and
never records usage for failed or errored searches (SerpApi does not charge
for them). The API key is never logged, cached or put in an error message.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import date

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import QuotaUsage
from app.services.cache import ResponseCache
from app.services.quota import QuotaTracker

SERPAPI_URL = "https://serpapi.com/search.json"
SERVICE = "serpapi"
TIMEOUT_SECONDS = 30


class SerpApiError(RuntimeError):
    """A SerpApi request failed (not charged)."""


class SerpApiNotConfigured(SerpApiError):
    """No SERPAPIKEY is set."""


class SerpApiBudgetExceeded(SerpApiError):
    """The monthly search cap has been reached."""


@dataclass(frozen=True)
class SerpApiUsage:
    month: str  # "YYYY-MM"
    used: int
    cap: int
    remaining: int


def _next_month_start(day: date) -> date:
    return date(day.year + 1, 1, 1) if day.month == 12 else date(day.year, day.month + 1, 1)


class SerpApiClient:
    def __init__(self, db: Session, *, http: httpx.Client | None = None) -> None:
        self.db = db
        self.http = http
        self.quota = QuotaTracker(db, service=SERVICE)
        self.cache = ResponseCache(db, service=SERVICE)

    def usage(self) -> SerpApiUsage:
        today = self.quota.today
        start = today.replace(day=1)
        used = self.db.scalar(
            select(func.coalesce(func.sum(QuotaUsage.units_used), 0)).where(
                QuotaUsage.service == SERVICE,
                QuotaUsage.day >= start,
                QuotaUsage.day < _next_month_start(today),
            )
        )
        cap = get_settings().serpapi_monthly_cap
        return SerpApiUsage(
            month=today.strftime("%Y-%m"), used=int(used), cap=cap, remaining=max(cap - int(used), 0)
        )

    def get(self, engine: str, params: dict[str, str | int], *, cache_days: int = 7) -> dict:
        key = get_settings().serpapi_key
        if not key:
            raise SerpApiNotConfigured("SerpApi is not configured: add SERPAPIKEY to backend/.env.")

        digest = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
        cache_key = f"{engine}?{digest}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        usage = self.usage()
        if usage.used >= usage.cap:
            raise SerpApiBudgetExceeded(
                f"SerpApi monthly cap reached ({usage.used}/{usage.cap} searches in {usage.month})."
            )

        payload = self._fetch(engine, params, key)
        search_params = payload.get("search_parameters")
        if isinstance(search_params, dict):
            search_params.pop("api_key", None)

        self.quota.record(SERVICE, 1)
        self.cache.set(cache_key, payload, cache_days * 86400)
        return payload

    def _fetch(self, engine: str, params: dict[str, str | int], key: str) -> dict:
        query = {**params, "engine": engine, "api_key": key}
        client = self.http or httpx.Client()
        try:
            try:
                response = client.get(SERPAPI_URL, params=query, timeout=TIMEOUT_SECONDS)
            except httpx.HTTPError as exc:
                raise SerpApiError(f"Cannot reach SerpApi ({type(exc).__name__}).") from None
        finally:
            if self.http is None:
                client.close()

        try:
            payload = response.json()
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            raise SerpApiError(f"SerpApi returned an unreadable response (HTTP {response.status_code}).")
        error = payload.get("error")
        if response.status_code != 200 or error:
            message = str(error) if error else f"SerpApi returned HTTP {response.status_code}."
            raise SerpApiError(message.replace(key, "***"))
        return payload
