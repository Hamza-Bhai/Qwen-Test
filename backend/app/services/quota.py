"""YouTube Data API v3 quota tracking.

Every endpoint costs a known number of quota units; we record usage per UTC
day so sync jobs can refuse to run when the daily budget is exhausted instead
of hammering the API into a quota error.
"""

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import QuotaUsage

# Documented unit costs for YouTube Data API v3 endpoints.
QUOTA_COSTS = {
    "channels.list": 1,
    "channels.list_for_handle": 1,  # channels.list with forHandle param
    "playlistItems.list": 1,
    "videos.list": 1,
    "search.list": 100,
}


class QuotaExceededError(RuntimeError):
    """Raised when an operation would exceed the configured daily quota."""


class QuotaTracker:
    def __init__(self, db: Session, service: str = "youtube") -> None:
        self.db = db
        self.service = service
        self.settings = get_settings()

    @property
    def today(self) -> date:
        return datetime.now(timezone.utc).date()

    def _row(self, day: date) -> QuotaUsage:
        row = self.db.execute(
            select(QuotaUsage).where(
                QuotaUsage.service == self.service, QuotaUsage.day == day
            )
        ).scalar_one_or_none()
        if row is None:
            row = QuotaUsage(service=self.service, day=day, units_used=0)
            self.db.add(row)
            try:
                self.db.commit()
            except IntegrityError:  # concurrent insert
                self.db.rollback()
                row = self.db.execute(
                    select(QuotaUsage).where(
                        QuotaUsage.service == self.service, QuotaUsage.day == day
                    )
                ).scalar_one()
        return row

    def used_today(self) -> int:
        return self._row(self.today).units_used

    def remaining_today(self) -> int:
        return max(self.settings.youtube_quota_daily_limit - self.used_today(), 0)

    def check_available(self, units: int) -> None:
        if self.remaining_today() < units:
            raise QuotaExceededError(
                f"YouTube daily quota exhausted: {self.used_today()} used, "
                f"{units} needed, limit {self.settings.youtube_quota_daily_limit}."
            )

    def record(self, endpoint: str, units: int | None = None) -> int:
        """Add usage for an executed request; returns units recorded."""
        cost = units if units is not None else QUOTA_COSTS.get(endpoint, 1)
        row = self._row(self.today)
        row.units_used += cost
        self.db.commit()
        return cost
