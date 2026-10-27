"""Add a per-record Office holiday flag and enforce zero production.

Revision ID: d7c8e2f1a609
Revises: b2a6f3d8c915
"""
from alembic import op
import sqlalchemy as sa

revision = "d7c8e2f1a609"
down_revision = "b2a6f3d8c915"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("manual_daily_records", sa.Column("office_holiday", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_check_constraint("ck_manual_daily_records_holiday_no_work", "manual_daily_records",
                               "NOT office_holiday OR (production_count = 0 AND pvp_count = 0 AND foundation_count = 0 AND COALESCE(foundation_hours, 0) = 0 AND tech_issues_downtime_hours = 0 AND no_inventory_idle_time_hours = 0 AND leave_hours = 0 AND meeting_engagement_hours = 0 AND meeting_type IS NULL AND (meetings IS NULL OR meetings::jsonb IN ('null'::jsonb, '[]'::jsonb)))")


def downgrade():
    op.drop_constraint("ck_manual_daily_records_holiday_no_work", "manual_daily_records", type_="check")
    op.drop_column("manual_daily_records", "office_holiday")
