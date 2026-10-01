"""Use the half-hour threshold for inferred historical meeting types.

Revision ID: a7e5d91c2b30
Revises: f4b8c2d7e901
"""

from alembic import op


revision = "a7e5d91c2b30"
down_revision = "f4b8c2d7e901"
branch_labels = None
depends_on = None


def upgrade():
    # Correct only untyped records and single entries matching the earlier
    # automatic backfill. Explicit choices and multi-meeting entries stay put.
    op.execute("""
        UPDATE manual_daily_records
        SET meeting_type = CASE
                WHEN meeting_engagement_hours <= 0.5 THEN 'Huddle'
                ELSE 'Others'
            END,
            meetings = json_build_array(json_build_object(
                'type', CASE
                    WHEN meeting_engagement_hours <= 0.5 THEN 'Huddle'
                    ELSE 'Others'
                END,
                'hours', meeting_engagement_hours::text
            ))
        WHERE meeting_engagement_hours > 0
          AND meeting_type IS DISTINCT FROM CASE
                WHEN meeting_engagement_hours <= 0.5 THEN 'Huddle'
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
    # Inferred classifications cannot be separated from later user edits.
    pass
