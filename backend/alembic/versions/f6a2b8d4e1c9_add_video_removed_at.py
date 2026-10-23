"""add video removed_at

Marks videos that a sync no longer finds on YouTube (deleted or private).

Revision ID: f6a2b8d4e1c9
Revises: e5f1a9c3d7b2
Create Date: 2026-10-23 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f6a2b8d4e1c9'
down_revision: Union[str, None] = 'e5f1a9c3d7b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('videos', schema=None) as batch_op:
        batch_op.add_column(sa.Column('removed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('videos', schema=None) as batch_op:
        batch_op.drop_column('removed_at')
