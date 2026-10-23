"""Growth Studio tables (all prefixed ``studio_``).

Kept apart from ``app.db.models`` so the public-channel feature is untouched.
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models import utcnow


class StudioSetting(Base):
    """Small key/value store for Studio settings (e.g. niche keywords)."""

    __tablename__ = "studio_setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[list | dict | str | int | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
