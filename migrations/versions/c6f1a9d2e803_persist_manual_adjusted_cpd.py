"""Persist manual daily targets and adjusted CPD, backfilling existing rows.

Revision ID: c6f1a9d2e803
Revises: b8f6e02d3c41
"""
from alembic import op
import sqlalchemy as sa

revision = "c6f1a9d2e803"
down_revision = "b8f6e02d3c41"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("manual_daily_records", sa.Column("daily_target", sa.Integer(), nullable=True))
    op.add_column("manual_daily_records", sa.Column("adjusted_cpd", sa.Numeric(12, 2), nullable=True))
    # Take an initial snapshot using existing stage history and manual hours.
    # JSON null is treated like SQL NULL, matching the ORM's legacy fallback.
    # Unassigned/Training users without a rule retain null targets and CPD.
    op.execute("""
        WITH snapshots AS (
            SELECT record.id, rule.daily_target,
                ROUND(GREATEST(0, 8
                    - record.tech_issues_downtime_hours
                    - record.no_inventory_idle_time_hours
                    - record.leave_hours
                    - CASE
                        WHEN json_typeof(record.meetings) = 'array' THEN (
                            SELECT COALESCE(SUM((meeting->>'hours')::numeric), 0)
                            FROM json_array_elements(record.meetings) AS meeting
                            WHERE meeting->>'type' IS DISTINCT FROM 'Huddle'
                        )
                        WHEN record.meeting_type = 'Huddle' THEN 0
                        ELSE record.meeting_engagement_hours
                      END
                ) * rule.daily_target / 8, 2) AS adjusted_cpd
            FROM manual_daily_records AS record
            LEFT JOIN user_stage_periods AS period
                ON period.user_id = record.user_id
                AND period.start_date <= record.record_date
                AND (period.end_date IS NULL OR period.end_date >= record.record_date)
            LEFT JOIN stage_target_rules AS rule
                ON rule.stage_code = period.stage_code
                AND rule.effective_from <= record.record_date
                AND (rule.effective_to IS NULL OR rule.effective_to > record.record_date)
        )
        UPDATE manual_daily_records AS record
        SET daily_target = snapshots.daily_target,
            adjusted_cpd = snapshots.adjusted_cpd
        FROM snapshots
        WHERE record.id = snapshots.id
    """)


def downgrade():
    op.drop_column("manual_daily_records", "adjusted_cpd")
    op.drop_column("manual_daily_records", "daily_target")
