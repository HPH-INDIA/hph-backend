"""combined program daily targets

Revision ID: 44a1e2e48e73
Revises: 04bba93362a2
Create Date: 2026-10-08 21:11:20.228021

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '44a1e2e48e73'
down_revision = '04bba93362a2'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("manual_daily_records", "daily_target", existing_type=sa.Integer(), type_=sa.Numeric(16, 6))
    op.add_column("manual_daily_records", sa.Column("pvp_daily_target", sa.Integer(), nullable=True))
    op.add_column("manual_daily_records", sa.Column("foundation_daily_target", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("manual_daily_records", "foundation_daily_target")
    op.drop_column("manual_daily_records", "pvp_daily_target")
    op.alter_column("manual_daily_records", "daily_target", existing_type=sa.Numeric(16, 6), type_=sa.Integer(),
                    postgresql_using="round(daily_target)::integer")
