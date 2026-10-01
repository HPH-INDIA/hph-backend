"""Apply the corrected historical meeting categories.

Revision ID: f4b8c2d7e901
Revises: e8a9c1d3f2b4
"""

from alembic import op


revision = "f4b8c2d7e901"
down_revision = "e8a9c1d3f2b4"
branch_labels = None
depends_on = None


def upgrade():
    # Reclassify only a single inferred meeting matching the earlier backfill,
    # or an untyped legacy record. Keep user-chosen and multi-meeting types.
    op.execute("""
        UPDATE manual_daily_records
        SET meeting_type = CASE
                WHEN meeting_engagement_hours <= 3 THEN 'Huddle'
                ELSE 'Others'
            END,
            meetings = json_build_array(json_build_object(
                'type', CASE
                    WHEN meeting_engagement_hours <= 3 THEN 'Huddle'
                    ELSE 'Others'
                END,
                'hours', meeting_engagement_hours::text
            ))
        WHERE meeting_engagement_hours > 0
          AND meeting_type IS DISTINCT FROM CASE
                WHEN meeting_engagement_hours <= 3 THEN 'Huddle'
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
    # The inferred values cannot be distinguished from later user edits.
    pass
