"""Add Microsoft Entra identity links and transactions without retiring passwords."""
from alembic import op
import sqlalchemy as sa

revision = "c3e7a9012b46"
down_revision = "b2a6f3d8c915"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("users", "password_hash", existing_type=sa.String(255), nullable=True)
    op.add_column("sessions", sa.Column("auth_method", sa.String(16), nullable=False, server_default="password"))
    op.create_table("external_identities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, unique=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("object_id", sa.String(36), nullable=False),
        sa.Column("account_type", sa.String(16), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("linked_by_user_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("last_authenticated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tenant_id", "object_id", name="uq_external_identity_subject"),
        sa.CheckConstraint("account_type IN ('member', 'guest')", name="ck_identity_account_type"))
    op.create_table("identity_audits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("operator", sa.String(128)),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("auth_transactions",
        sa.Column("state_hash", sa.String(64), primary_key=True),
        sa.Column("browser_hash", sa.String(64), nullable=False),
        sa.Column("encrypted_flow", sa.Text(), nullable=False),
        sa.Column("return_to", sa.String(2048), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_auth_transactions_expires_at", "auth_transactions", ["expires_at"])


def downgrade():
    # Rollback must never invent passwords for Microsoft-only accounts.
    if op.get_bind().execute(sa.text("SELECT 1 FROM users WHERE password_hash IS NULL LIMIT 1")).first():
        raise RuntimeError("Cannot downgrade while Microsoft-only accounts exist.")
    op.drop_table("auth_transactions")
    op.drop_table("identity_audits")
    op.drop_table("external_identities")
    op.drop_column("sessions", "auth_method")
    op.alter_column("users", "password_hash", existing_type=sa.String(255), nullable=False)
