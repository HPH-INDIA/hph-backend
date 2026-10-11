"""Add audited, time-limited lead coverage grants."""

from alembic import op
import sqlalchemy as sa

revision = "ca42d801ef76"
down_revision = "8acb1f8e5b44"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "lead_coverages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_lead_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("covering_lead_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(2000), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("decided_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("source_lead_id <> covering_lead_id", name="ck_lead_coverage_distinct_leads"),
        sa.CheckConstraint("ends_on >= starts_on", name="ck_lead_coverage_date_range"),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected', 'cancelled')", name="ck_lead_coverage_status"),
    )
    op.create_index("ix_lead_coverage_source_dates", "lead_coverages", ["source_lead_id", "starts_on", "ends_on"])
    op.create_index("ix_lead_coverage_covering_status", "lead_coverages", ["covering_lead_id", "status"])
    op.execute("ALTER TABLE lead_coverages ENABLE ROW LEVEL SECURITY")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hph_app') THEN
                GRANT SELECT, INSERT, UPDATE ON lead_coverages TO hph_app;
                GRANT USAGE, SELECT ON SEQUENCE lead_coverages_id_seq TO hph_app;
                CREATE POLICY lead_coverages_backend ON lead_coverages
                    TO hph_app USING (true) WITH CHECK (true);
            END IF;
        END $$;
    """)


def downgrade():
    op.drop_table("lead_coverages")
