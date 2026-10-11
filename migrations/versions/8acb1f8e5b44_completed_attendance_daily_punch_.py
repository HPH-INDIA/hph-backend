"""Completed attendance daily punch summaries

Revision ID: 8acb1f8e5b44
Revises: f10018702a3a
Create Date: 2026-10-11 15:11:32.489170

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8acb1f8e5b44'
down_revision = 'f10018702a3a'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('login_hours_upload_batches', sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('login_hours_upload_batches', sa.Column('attendance_dates', sa.JSON(), nullable=False, server_default='[]'))
    op.add_column('login_hours_upload_batches', sa.Column('punch_user_ids_by_date', sa.JSON(), nullable=False, server_default='{}'))
    # Older chunk uploads never recorded a completion marker or immutable
    # source snapshot. Do not infer complete daily coverage from partial rows.
    op.create_index('ix_login_hours_batches_completed', 'login_hours_upload_batches', ['completed_at', 'id'])


def downgrade():
    op.drop_index('ix_login_hours_batches_completed', table_name='login_hours_upload_batches')
    op.drop_column('login_hours_upload_batches', 'punch_user_ids_by_date')
    op.drop_column('login_hours_upload_batches', 'attendance_dates')
    op.drop_column('login_hours_upload_batches', 'completed_at')
