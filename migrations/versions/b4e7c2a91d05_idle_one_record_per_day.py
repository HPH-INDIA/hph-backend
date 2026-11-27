"""Idle time: one record per person per day - tickets stop splitting the hours.

The day's idle hours (tech downtime + no-inventory idle) already live once on
manual_daily_records (unique per user per day). manual_record_tickets kept a
per-ticket split of those hours, which reports showed as several idle rows
for the same person's same day. From now on a link only says which IT ticket
(practice) blocked that day; reports read the hours from the daily record.

Upgrade keeps every existing link and clears the old split (hours -> NULL).
Downgrade cannot recover the original split: it spreads each day's idle hours
evenly over that day's tickets so the old code's sums still match the record.

Revision ID: b4e7c2a91d05
Revises: c7e1a4d2b9f6
"""
import sqlalchemy as sa
from alembic import op

revision = "b4e7c2a91d05"
down_revision = "c7e1a4d2b9f6"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("manual_record_tickets", "hours", existing_type=sa.Numeric(4, 2), nullable=True)
    op.execute(sa.text("UPDATE manual_record_tickets SET hours = NULL"))


def downgrade():
    op.execute(sa.text(
        "UPDATE manual_record_tickets AS link SET hours = LEAST(10, ROUND("
        "  (COALESCE(r.tech_issues_downtime_hours, 0) + COALESCE(r.no_inventory_idle_time_hours, 0))"
        "  / (SELECT COUNT(*) FROM manual_record_tickets other WHERE other.manual_record_id = link.manual_record_id),"
        "  2)) "
        "FROM manual_daily_records r WHERE r.id = link.manual_record_id"))
    op.execute(sa.text("UPDATE manual_record_tickets SET hours = 0 WHERE hours IS NULL"))
    op.alter_column("manual_record_tickets", "hours", existing_type=sa.Numeric(4, 2), nullable=False)
