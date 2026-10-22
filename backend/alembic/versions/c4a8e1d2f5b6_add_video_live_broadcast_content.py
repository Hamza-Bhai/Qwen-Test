"""add video live_broadcast_content

Revision ID: c4a8e1d2f5b6
Revises: b7e2d4f19a30
Create Date: 2026-10-22 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4a8e1d2f5b6'
down_revision: Union[str, None] = 'b7e2d4f19a30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('videos', schema=None) as batch_op:
        batch_op.add_column(sa.Column('live_broadcast_content', sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('videos', schema=None) as batch_op:
        batch_op.drop_column('live_broadcast_content')
