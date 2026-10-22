"""Quota / cache observability schemas."""

from datetime import date

from pydantic import BaseModel


class QuotaStatus(BaseModel):
    service: str
    day: date
    units_used: int
    daily_limit: int
    remaining: int


class CacheStats(BaseModel):
    entries: int
    valid_entries: int
