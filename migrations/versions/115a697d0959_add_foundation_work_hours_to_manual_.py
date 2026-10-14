"""Add Foundation work hours to manual records

Revision ID: 115a697d0959
Revises: fa3d6c01490c
Create Date: 2026-10-13 22:15:09.442272

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '115a697d0959'
down_revision = 'fa3d6c01490c'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("manual_daily_records", sa.Column("foundation_hours", sa.Numeric(4, 2), nullable=True))
    op.create_check_constraint("ck_manual_daily_records_foundation_hours", "manual_daily_records",
                               "foundation_hours >= 0 AND foundation_hours <= 10")


def downgrade():
    op.drop_constraint("ck_manual_daily_records_foundation_hours", "manual_daily_records", type_="check")
    op.drop_column("manual_daily_records", "foundation_hours")
