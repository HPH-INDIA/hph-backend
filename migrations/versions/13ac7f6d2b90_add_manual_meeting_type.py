"""add manual meeting type

Revision ID: 13ac7f6d2b90
Revises: b4c7d9e2f105
"""

from alembic import op
import sqlalchemy as sa


revision = "13ac7f6d2b90"
down_revision = "b4c7d9e2f105"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("manual_daily_records", sa.Column("meeting_type", sa.String(32), nullable=True))
    op.create_check_constraint(
        "ck_manual_daily_records_meeting_type",
        "manual_daily_records",
        "meeting_type IN ('Assessment', 'One-O-One', 'Meeting', 'Training', 'Huddle', 'PKT', 'Others')",
    )


def downgrade():
    op.drop_constraint("ck_manual_daily_records_meeting_type", "manual_daily_records", type_="check")
    op.drop_column("manual_daily_records", "meeting_type")
