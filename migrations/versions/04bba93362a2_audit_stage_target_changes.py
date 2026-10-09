"""audit stage target changes

Revision ID: 04bba93362a2
Revises: f469f0fdd1f2
Create Date: 2026-10-08 21:04:44.047074

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '04bba93362a2'
down_revision = 'f469f0fdd1f2'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE stage_target_changes (
            id serial PRIMARY KEY,
            program varchar(16) NOT NULL,
            stage_code varchar(32) NOT NULL,
            apply_from varchar(16) NOT NULL,
            effective_from date NOT NULL,
            daily_target integer NOT NULL,
            created_by_id integer NOT NULL REFERENCES users(id),
            reason text,
            previous_rules json NOT NULL,
            recalculated_records integer NOT NULL DEFAULT 0,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_target_change_program CHECK (program IN ('main','foundation')),
            CONSTRAINT ck_target_change_scope CHECK (apply_from IN ('today','program_start','scheduled')),
            CONSTRAINT ck_target_change_nonnegative CHECK (daily_target >= 0 AND recalculated_records >= 0)
        );
        CREATE INDEX ix_stage_target_changes_created_by_id ON stage_target_changes(created_by_id);
        ALTER TABLE stage_target_changes ENABLE ROW LEVEL SECURITY;
    """)


def downgrade():
    op.drop_table("stage_target_changes")
