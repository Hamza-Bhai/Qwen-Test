"""drop unused video content_width and content_height

The Data API's contentDetails.dimension is "2d"/"3d", never "WxH", so these
columns were always NULL.

Revision ID: e5f1a9c3d7b2
Revises: c4a8e1d2f5b6
Create Date: 2026-10-23 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5f1a9c3d7b2'
down_revision: Union[str, None] = 'c4a8e1d2f5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('videos', schema=None) as batch_op:
        batch_op.drop_column('content_height')
        batch_op.drop_column('content_width')


def downgrade() -> None:
    with op.batch_alter_table('videos', schema=None) as batch_op:
        batch_op.add_column(sa.Column('content_width', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('content_height', sa.Integer(), nullable=True))
