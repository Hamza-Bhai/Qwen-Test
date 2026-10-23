"""add studio_setting

Key/value settings for Growth Studio (niche keywords, later more).

Revision ID: c9d1e2f3a4b5
Revises: b8c4d0f3e6a2
Create Date: 2026-10-23 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c9d1e2f3a4b5'
down_revision: Union[str, None] = 'b8c4d0f3e6a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The dev backend's init_db() may already have created the table (create_all).
    if sa.inspect(op.get_bind()).has_table('studio_setting'):
        return
    op.create_table(
        'studio_setting',
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('value', sa.JSON(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('key'),
    )


def downgrade() -> None:
    op.drop_table('studio_setting')
