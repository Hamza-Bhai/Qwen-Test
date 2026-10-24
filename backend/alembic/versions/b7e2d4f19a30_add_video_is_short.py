"""add video is_short

Revision ID: b7e2d4f19a30
Revises: 864e8963ab7d
Create Date: 2026-10-22 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7e2d4f19a30'
down_revision: Union[str, None] = '864e8963ab7d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('videos', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_short', sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('videos', schema=None) as batch_op:
        batch_op.drop_column('is_short')
