"""Classify historical manual meeting hours without removing any records.

Revision ID: c9f2a4d18e70
Revises: 7c2d6f1a4b90
"""

from alembic import op


revision = "c9f2a4d18e70"
down_revision = "7c2d6f1a4b90"
branch_labels = None
depends_on = None


def upgrade():
    # Preserve explicit types and meeting breakdowns that may have been added
    # after the columns were created. Zero hours means there was no meeting.
    op.execute("""
        UPDATE manual_daily_records
        SET meeting_type = COALESCE(
                meeting_type,
                CASE WHEN meeting_engagement_hours < 0.25 THEN 'Huddle' ELSE 'Others' END
            ),
            meetings = json_build_array(json_build_object(
                'type', COALESCE(
                    meeting_type,
                    CASE WHEN meeting_engagement_hours < 0.25 THEN 'Huddle' ELSE 'Others' END
                ),
                'hours', meeting_engagement_hours::text
            ))
        WHERE meeting_engagement_hours > 0
          AND meetings IS NULL
    """)


def downgrade():
    # The backfill cannot be distinguished from later user edits. Leave the
    # classified data in place if the Alembic revision is rolled back.
    pass
