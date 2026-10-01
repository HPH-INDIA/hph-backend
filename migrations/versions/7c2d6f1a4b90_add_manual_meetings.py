"""Store multiple meeting entries within each manual daily record.

Revision ID: 7c2d6f1a4b90
Revises: 13ac7f6d2b90
"""

from alembic import op
import sqlalchemy as sa


revision = "7c2d6f1a4b90"
down_revision = "13ac7f6d2b90"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("manual_daily_records", sa.Column("meetings", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("manual_daily_records", "meetings")
