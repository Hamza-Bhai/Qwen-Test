"""add competitor tables

Growth Studio watchlist: competitors, their videos and per-sync snapshots.

Revision ID: d2e3f4a5b6c7
Revises: c9d1e2f3a4b5
Create Date: 2026-10-23 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, None] = 'c9d1e2f3a4b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The dev backend's init_db() may already have created these (create_all).
    inspector = sa.inspect(op.get_bind())

    if not inspector.has_table('studio_competitor'):
        op.create_table(
            'studio_competitor',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('youtube_channel_id', sa.String(length=64), nullable=False),
            sa.Column('title', sa.String(length=255), nullable=False),
            sa.Column('handle', sa.String(length=100), nullable=True),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('thumbnail_url', sa.String(length=512), nullable=True),
            sa.Column('country', sa.String(length=8), nullable=True),
            sa.Column('uploads_playlist_id', sa.String(length=64), nullable=True),
            sa.Column('subscriber_count', sa.Integer(), nullable=True),
            sa.Column('video_count', sa.Integer(), nullable=True),
            sa.Column('view_count', sa.Integer(), nullable=True),
            sa.Column('channel_created_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('status', sa.String(length=16), nullable=False),
            sa.Column('source', sa.String(length=16), nullable=False),
            sa.Column('discovery_reason', sa.JSON(), nullable=True),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('added_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('status_changed_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('last_viewed_at', sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(
            'ix_studio_competitor_youtube_channel_id', 'studio_competitor', ['youtube_channel_id'], unique=True
        )
        op.create_index('ix_studio_competitor_status', 'studio_competitor', ['status'])

    if not inspector.has_table('studio_competitor_video'):
        op.create_table(
            'studio_competitor_video',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('competitor_id', sa.Integer(), nullable=False),
            sa.Column('youtube_video_id', sa.String(length=32), nullable=False),
            sa.Column('title', sa.String(length=512), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('thumbnail_url', sa.String(length=512), nullable=True),
            sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('duration_seconds', sa.Integer(), nullable=True),
            sa.Column('is_short', sa.Boolean(), nullable=True),
            sa.Column('live_broadcast_content', sa.String(length=16), nullable=True),
            sa.Column('tags', sa.JSON(), nullable=True),
            sa.Column('hashtags', sa.JSON(), nullable=True),
            sa.Column('view_count', sa.Integer(), nullable=False),
            sa.Column('like_count', sa.Integer(), nullable=True),
            sa.Column('comment_count', sa.Integer(), nullable=True),
            sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('hook_text', sa.Text(), nullable=True),
            sa.Column('hook_segments', sa.JSON(), nullable=True),
            sa.ForeignKeyConstraint(['competitor_id'], ['studio_competitor.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_studio_competitor_video_competitor_id', 'studio_competitor_video', ['competitor_id'])
        op.create_index(
            'ix_studio_competitor_video_youtube_video_id',
            'studio_competitor_video',
            ['youtube_video_id'],
            unique=True,
        )

    if not inspector.has_table('studio_competitor_video_snapshot'):
        op.create_table(
            'studio_competitor_video_snapshot',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('video_id', sa.Integer(), nullable=False),
            sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('view_count', sa.Integer(), nullable=False),
            sa.Column('like_count', sa.Integer(), nullable=True),
            sa.Column('comment_count', sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(['video_id'], ['studio_competitor_video.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(
            'ix_studio_competitor_video_snapshot_video_id', 'studio_competitor_video_snapshot', ['video_id']
        )


def downgrade() -> None:
    op.drop_table('studio_competitor_video_snapshot')
    op.drop_table('studio_competitor_video')
    op.drop_table('studio_competitor')
