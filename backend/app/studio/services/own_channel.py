"""Which public channel is the owner's own.

The owner's channel is one of the public ``Channel`` rows (read only here).
A saved choice wins; with nothing saved, a single active channel is used.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Channel
from app.studio.models import StudioSetting

OWN_CHANNEL_KEY = "own_channel_id"


def _active_channels(db: Session) -> list[Channel]:
    return list(
        db.scalars(select(Channel).where(Channel.archived_at.is_(None)).order_by(Channel.id)).all()
    )


def get_own_channel(db: Session) -> Channel | None:
    row = db.get(StudioSetting, OWN_CHANNEL_KEY)
    if row is not None and isinstance(row.value, int):
        channel = db.get(Channel, row.value)
        if channel is not None and channel.archived_at is None:
            return channel
    active = _active_channels(db)
    return active[0] if len(active) == 1 else None


def list_own_channel_candidates(db: Session) -> list[Channel]:
    return _active_channels(db)


def set_own_channel(db: Session, channel_id: int) -> Channel:
    channel = db.get(Channel, channel_id)
    if channel is None or channel.archived_at is not None:
        raise ValueError("That channel is not tracked (or was removed).")
    row = db.get(StudioSetting, OWN_CHANNEL_KEY)
    if row is None:
        db.add(StudioSetting(key=OWN_CHANNEL_KEY, value=channel_id))
    else:
        row.value = channel_id
    db.commit()
    return channel
