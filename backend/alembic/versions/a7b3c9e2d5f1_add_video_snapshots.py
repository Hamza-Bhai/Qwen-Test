"""add video_snapshots

Per-sync history of each video's public stats (views, likes, comments).

Revision ID: a7b3c9e2d5f1
Revises: f6a2b8d4e1c9
Create Date: 2026-10-23 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7b3c9e2d5f1'
down_revision: Union[str, None] = 'f6a2b8d4e1c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'video_snapshots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('video_id', sa.Integer(), nullable=False),
        sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('view_count', sa.Integer(), nullable=False),
        sa.Column('like_count', sa.Integer(), nullable=True),
        sa.Column('comment_count', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['video_id'], ['videos.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_video_snapshots_video_id', 'video_snapshots', ['video_id'])
    op.create_index('ix_video_snapshots_video_captured', 'video_snapshots',
                    ['video_id', 'captured_at'])


def downgrade() -> None:
    op.drop_index('ix_video_snapshots_video_captured', table_name='video_snapshots')
    op.drop_index('ix_video_snapshots_video_id', table_name='video_snapshots')
    op.drop_table('video_snapshots')
