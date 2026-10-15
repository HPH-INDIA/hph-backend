"""Add hold reason and held date to Kairon chart records."""
from alembic import op
import sqlalchemy as sa

revision = "b2a6f3d8c915"
down_revision = "115a697d0959"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("kairon_chart_records", sa.Column("last_hold_reason", sa.Text(), nullable=True))
    op.add_column("kairon_chart_records", sa.Column("held_on", sa.Date(), nullable=True))
    op.create_check_constraint(
        "ck_kairon_hold_metadata_status", "kairon_chart_records",
        "status = 'On Hold' OR (last_hold_reason IS NULL AND held_on IS NULL)",
    )


def downgrade():
    op.drop_constraint("ck_kairon_hold_metadata_status", "kairon_chart_records", type_="check")
    op.drop_column("kairon_chart_records", "held_on")
    op.drop_column("kairon_chart_records", "last_hold_reason")
