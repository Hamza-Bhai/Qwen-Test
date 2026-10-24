"""Database-backed response cache with TTL.

Reusable by any external API service. Cached payloads always come from real
API responses — this module never synthesizes data (rule #10).
"""

import json
from collections.abc import Collection
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import ApiCache


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


class ResponseCache:
    def __init__(self, db: Session, service: str = "youtube") -> None:
        self.db = db
        self.service = service

    def get(self, key: str) -> dict | None:
        """Return the cached payload if present and unexpired."""
        row = self.db.execute(
            select(ApiCache).where(
                ApiCache.service == self.service, ApiCache.cache_key == key
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        if _as_utc(row.expires_at) <= _utcnow():
            self.db.delete(row)
            self.db.commit()
            return None
        return row.response_json

    def set(self, key: str, payload: dict, ttl_seconds: int) -> None:
        """Insert or replace a cache entry."""
        row = self.db.execute(
            select(ApiCache).where(
                ApiCache.service == self.service, ApiCache.cache_key == key
            )
        ).scalar_one_or_none()
        expires = _utcnow() + timedelta(seconds=ttl_seconds)
        if row is None:
            self.db.add(
                ApiCache(
                    service=self.service,
                    cache_key=key,
                    response_json=payload,
                    expires_at=expires,
                )
            )
        else:
            row.response_json = payload
            row.expires_at = expires
        self.db.commit()

    def purge_matching(self, tokens: Collection[str | None]) -> int:
        """Delete entries whose key or stored response mentions any token.

        Cache rows are not linked to channels by a foreign key, so removing a
        channel uses its YouTube ids (channel, uploads playlist, videos) to
        find the rows that only exist because of it.
        """
        tokens = {t for t in tokens if t}
        if not tokens:
            return 0
        removed = 0
        rows = self.db.execute(
            select(ApiCache).where(ApiCache.service == self.service)
        ).scalars()
        for row in list(rows):
            haystack = row.cache_key + json.dumps(row.response_json)
            if any(t in haystack for t in tokens):
                self.db.delete(row)
                removed += 1
        self.db.commit()
        return removed

    def purge_expired(self) -> int:
        result = self.db.execute(
            delete(ApiCache).where(ApiCache.expires_at <= _utcnow())
        )
        self.db.commit()
        return result.rowcount or 0
