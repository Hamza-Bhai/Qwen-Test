"""add advisor tables

Growth Studio milestone D: advisor/ideas reports, video ideas and their outcomes.

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-10-24 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f4a5b6c7d8e9'
down_revision: Union[str, None] = 'e3f4a5b6c7d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _connection_id() -> sa.Column:
    return sa.Column('connection_id', sa.Integer(),
                     sa.ForeignKey('studio_google_connection.id', ondelete='CASCADE'), nullable=False)


def upgrade() -> None:
    # The dev backend's init_db() may already have created these (create_all).
    inspector = sa.inspect(op.get_bind())

    if not inspector.has_table('studio_advisor_report'):
        op.create_table(
            'studio_advisor_report',
            sa.Column('id', sa.Integer(), primary_key=True),
            _connection_id(),
            sa.Column('kind', sa.String(length=16), nullable=False),
            sa.Column('evidence_hash', sa.String(length=64), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('payload', sa.JSON(), nullable=False),
            sa.Column('model', sa.String(length=100), nullable=False),
        )
        op.create_index('ix_studio_advisor_report_connection_id', 'studio_advisor_report', ['connection_id'])
        op.create_index('ix_studio_advisor_report_evidence_hash', 'studio_advisor_report', ['evidence_hash'])

    if not inspector.has_table('studio_idea'):
        op.create_table(
            'studio_idea',
            sa.Column('id', sa.Integer(), primary_key=True),
            _connection_id(),
            sa.Column('report_id', sa.Integer(),
                      sa.ForeignKey('studio_advisor_report.id', ondelete='SET NULL'), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('status', sa.String(length=16), nullable=False),
            sa.Column('format', sa.String(length=8), nullable=False),
            sa.Column('topic', sa.String(length=255), nullable=False),
            sa.Column('payload', sa.JSON(), nullable=False),
            sa.Column('planned_date', sa.Date(), nullable=True),
            sa.Column('published_video_id', sa.String(length=32), nullable=True),
            sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('baseline', sa.JSON(), nullable=True),
        )
        op.create_index('ix_studio_idea_connection_id', 'studio_idea', ['connection_id'])
        op.create_index('ix_studio_idea_status', 'studio_idea', ['status'])

    if not inspector.has_table('studio_idea_outcome'):
        op.create_table(
            'studio_idea_outcome',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('idea_id', sa.Integer(), sa.ForeignKey('studio_idea.id', ondelete='CASCADE'), nullable=False),
            sa.Column('checkpoint', sa.Integer(), nullable=False),
            sa.Column('measured_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('views_per_day', sa.Float(), nullable=False),
            sa.Column('baseline_vpd', sa.Float(), nullable=True),
            sa.Column('ratio', sa.Float(), nullable=True),
            sa.Column('verdict', sa.String(length=10), nullable=False),
            sa.UniqueConstraint('idea_id', 'checkpoint'),
        )
        op.create_index('ix_studio_idea_outcome_idea_id', 'studio_idea_outcome', ['idea_id'])


def downgrade() -> None:
    for name in ('studio_idea_outcome', 'studio_idea', 'studio_advisor_report'):
        op.drop_table(name)
