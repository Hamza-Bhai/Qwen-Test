"""add channel archived_at

A channel removed with "keep my data" is archived instead of deleted.

Revision ID: b8c4d0f3e6a2
Revises: a7b3c9e2d5f1
Create Date: 2026-10-23 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b8c4d0f3e6a2'
down_revision: Union[str, None] = 'a7b3c9e2d5f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('channels', schema=None) as batch_op:
        batch_op.add_column(sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('channels', schema=None) as batch_op:
        batch_op.drop_column('archived_at')
