"""Correct the inferred meeting type threshold for historical records.

Revision ID: e8a9c1d3f2b4
Revises: c9f2a4d18e70
"""

from alembic import op


revision = "e8a9c1d3f2b4"
down_revision = "c9f2a4d18e70"
branch_labels = None
depends_on = None


def upgrade():
    # Only correct untyped records or a single meeting entry matching the
    # earlier automatic backfill. Preserve explicit types and breakdowns.
    op.execute("""
        UPDATE manual_daily_records
        SET meeting_type = CASE
                WHEN meeting_engagement_hours >= 3 THEN 'Huddle'
                ELSE 'Others'
            END,
            meetings = json_build_array(json_build_object(
                'type', CASE
                    WHEN meeting_engagement_hours >= 3 THEN 'Huddle'
                    ELSE 'Others'
                END,
                'hours', meeting_engagement_hours::text
            ))
        WHERE meeting_engagement_hours > 0
          AND meeting_type IS DISTINCT FROM CASE
                WHEN meeting_engagement_hours >= 3 THEN 'Huddle'
                ELSE 'Others'
            END
          AND (
              (meeting_type IS NULL AND meetings IS NULL)
              OR (
                  meeting_type IN ('Huddle', 'Others')
                  AND meetings::jsonb = jsonb_build_array(jsonb_build_object(
                      'type', meeting_type,
                      'hours', meeting_engagement_hours::text
                  ))
              )
          )
    """)


def downgrade():
    # The corrected values cannot be distinguished from later user edits.
    pass
