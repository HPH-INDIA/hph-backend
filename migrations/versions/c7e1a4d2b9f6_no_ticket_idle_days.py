"""No ticket - idle: a full idle day with no IT ticket gets a generic ticket.

Adds practices.is_system and the hidden "All practices (no ticket)" practice
those generic tickets use, and lets it_tickets hold source 'no_ticket' with a
NOTICKET-YYYYMMDD-<id> key (real tickets keep the VTEC-n rule).

Revision ID: c7e1a4d2b9f6
Revises: b3d9e1f2a7c4
"""
import sqlalchemy as sa
from alembic import op

revision = "c7e1a4d2b9f6"
down_revision = "b3d9e1f2a7c4"
branch_labels = None
depends_on = None

SYSTEM_PRACTICE = "All practices (no ticket)"


def upgrade():
    op.add_column("practices", sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.drop_constraint("ck_it_tickets_source", "it_tickets", type_="check")
    op.create_check_constraint("ck_it_tickets_source", "it_tickets", "source IN ('manual', 'import', 'no_ticket')")
    op.drop_constraint("ck_it_tickets_jira_key", "it_tickets", type_="check")
    op.create_check_constraint(
        "ck_it_tickets_jira_key", "it_tickets",
        "jira_key ~ '^VTEC-[0-9]+$' OR (source = 'no_ticket' AND jira_key ~ '^NOTICKET-[0-9]{8}-[A-Za-z0-9_-]+$')")
    op.execute(sa.text(
        "INSERT INTO practices (name, name_key, status, is_system, created_at, updated_at) "
        "SELECT :name, :key, 'Active', true, now(), now() "
        "WHERE NOT EXISTS (SELECT 1 FROM practices WHERE is_system)"
    ).bindparams(name=SYSTEM_PRACTICE, key=SYSTEM_PRACTICE.lower()))


def downgrade():
    op.execute("DELETE FROM it_tickets WHERE source = 'no_ticket'")
    op.execute("DELETE FROM practices WHERE is_system")
    op.drop_constraint("ck_it_tickets_jira_key", "it_tickets", type_="check")
    op.create_check_constraint("ck_it_tickets_jira_key", "it_tickets", "jira_key ~ '^VTEC-[0-9]+$'")
    op.drop_constraint("ck_it_tickets_source", "it_tickets", type_="check")
    op.create_check_constraint("ck_it_tickets_source", "it_tickets", "source IN ('manual', 'import')")
    op.drop_column("practices", "is_system")
