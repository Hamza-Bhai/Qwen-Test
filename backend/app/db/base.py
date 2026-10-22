"""Declarative base for ORM models.

Kept free of model imports so ``models.py`` can import it without a circular
dependency. Alembic's ``env.py`` imports ``app.db.models`` explicitly to
register all tables on ``Base.metadata``.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
