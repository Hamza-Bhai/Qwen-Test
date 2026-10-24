"""add my channel tables

Growth Studio milestone C: connected own channels (Google OAuth) and their
private analytics time series.

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-10-24 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e3f4a5b6c7d8'
down_revision: Union[str, None] = 'd2e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _connection_id() -> sa.Column:
    return sa.Column(
        'connection_id', sa.Integer(),
        sa.ForeignKey('studio_google_connection.id', ondelete='CASCADE'), nullable=False,
    )


def upgrade() -> None:
    # The dev backend's init_db() may already have created these (create_all).
    inspector = sa.inspect(op.get_bind())

    if not inspector.has_table('studio_google_connection'):
        op.create_table(
            'studio_google_connection',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('youtube_channel_id', sa.String(length=64), nullable=False),
            sa.Column('title', sa.String(length=255), nullable=False),
            sa.Column('thumbnail_url', sa.String(length=512), nullable=True),
            sa.Column('refresh_token', sa.Text(), nullable=True),
            sa.Column('scopes', sa.String(length=512), nullable=False),
            sa.Column('status', sa.String(length=20), nullable=False),
            sa.Column('last_error', sa.Text(), nullable=True),
            sa.Column('connected_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('last_pulled_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('reach_job_id', sa.String(length=64), nullable=True),
            sa.Column('reach_report_ids', sa.JSON(), nullable=False),
        )
        op.create_index('ix_studio_google_connection_youtube_channel_id', 'studio_google_connection',
                        ['youtube_channel_id'], unique=True)

    tables = {
        'studio_my_video': [
            sa.Column('youtube_video_id', sa.String(length=32), nullable=False),
            sa.Column('title', sa.String(length=512), nullable=False),
            sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('duration_seconds', sa.Integer(), nullable=True),
            sa.Column('is_short', sa.Boolean(), nullable=True),
            sa.Column('live_broadcast_content', sa.String(length=16), nullable=True),
            sa.UniqueConstraint('connection_id', 'youtube_video_id'),
        ],
        'studio_analytics_channel_daily': [
            sa.Column('day', sa.Date(), nullable=False),
            *[sa.Column(c, sa.Integer(), nullable=False) for c in ('views', 'watch_minutes', 'avg_view_duration_s')],
            sa.Column('avg_view_pct', sa.Float(), nullable=False),
            *[sa.Column(c, sa.Integer(), nullable=False)
              for c in ('subs_gained', 'subs_lost', 'likes', 'comments', 'shares')],
            sa.UniqueConstraint('connection_id', 'day'),
        ],
        'studio_analytics_video_daily': [
            sa.Column('youtube_video_id', sa.String(length=32), nullable=False),
            sa.Column('as_of', sa.Date(), nullable=False),
            *[sa.Column(c, sa.Integer(), nullable=False) for c in ('views', 'watch_minutes', 'avg_view_duration_s')],
            sa.Column('avg_view_pct', sa.Float(), nullable=False),
            sa.Column('subs_gained', sa.Integer(), nullable=False),
            sa.UniqueConstraint('connection_id', 'youtube_video_id', 'as_of'),
        ],
        'studio_analytics_traffic': [
            sa.Column('day', sa.Date(), nullable=False),
            sa.Column('source', sa.String(length=64), nullable=False),
            sa.Column('views', sa.Integer(), nullable=False),
            sa.Column('watch_minutes', sa.Integer(), nullable=False),
            sa.UniqueConstraint('connection_id', 'day', 'source'),
        ],
        'studio_analytics_retention': [
            sa.Column('youtube_video_id', sa.String(length=32), nullable=False),
            sa.Column('elapsed_ratio', sa.Float(), nullable=False),
            sa.Column('watch_ratio', sa.Float(), nullable=False),
            sa.Column('pulled_at', sa.DateTime(timezone=True), nullable=False),
        ],
        'studio_reach_daily': [
            sa.Column('day', sa.Date(), nullable=False),
            sa.Column('youtube_video_id', sa.String(length=32), nullable=False),
            sa.Column('impressions', sa.Integer(), nullable=False),
            sa.Column('clicks', sa.Float(), nullable=False),
            sa.UniqueConstraint('connection_id', 'day', 'youtube_video_id'),
        ],
    }
    for name, columns in tables.items():
        if inspector.has_table(name):
            continue
        op.create_table(name, sa.Column('id', sa.Integer(), primary_key=True), _connection_id(), *columns)
        op.create_index(f'ix_{name}_connection_id', name, ['connection_id'])
    if not any(ix['name'] == 'ix_studio_analytics_retention_youtube_video_id'
               for ix in sa.inspect(op.get_bind()).get_indexes('studio_analytics_retention')):
        op.create_index('ix_studio_analytics_retention_youtube_video_id', 'studio_analytics_retention',
                        ['youtube_video_id'])


def downgrade() -> None:
    for name in ('studio_reach_daily', 'studio_analytics_retention', 'studio_analytics_traffic',
                 'studio_analytics_video_daily', 'studio_analytics_channel_daily', 'studio_my_video',
                 'studio_google_connection'):
        op.drop_table(name)
