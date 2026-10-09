"""add user joining dates and foundation progression

Revision ID: f469f0fdd1f2
Revises: d3f8b0e52147
Create Date: 2026-10-08 20:06:33.101592

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f469f0fdd1f2'
down_revision = 'd3f8b0e52147'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("join_date", sa.Date(), nullable=True))
    op.execute("""
        CREATE TABLE user_stage_evidence (
            user_id integer PRIMARY KEY REFERENCES users(id),
            first_completed date,
            first_pvp_completed date,
            first_foundation_completed date,
            refreshed_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE foundation_target_rules (
            id serial PRIMARY KEY,
            stage_code varchar(32) NOT NULL,
            effective_from date NOT NULL,
            effective_to date,
            daily_target integer NOT NULL,
            created_by_id integer REFERENCES users(id),
            reason text,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_foundation_target_stage CHECK (stage_code IN ('W1','W2','W3','W4','Steady State')),
            CONSTRAINT ck_foundation_target_nonnegative CHECK (daily_target >= 0),
            CONSTRAINT ck_foundation_target_range CHECK (effective_to IS NULL OR effective_to > effective_from),
            CONSTRAINT ex_foundation_target_no_overlap EXCLUDE USING gist
                (stage_code WITH =, daterange(effective_from,effective_to) WITH &&)
        );
        CREATE INDEX ix_foundation_target_rules_created_by_id ON foundation_target_rules(created_by_id);
        ALTER TABLE user_stage_evidence ENABLE ROW LEVEL SECURITY;
        ALTER TABLE foundation_target_rules ENABLE ROW LEVEL SECURITY;
        INSERT INTO foundation_target_rules(stage_code,effective_from,daily_target,reason)
        VALUES ('W1','1900-01-01',7,'Initial Foundation weekly target'),
               ('W2','1900-01-01',14,'Initial Foundation weekly target'),
               ('W3','1900-01-01',20,'Initial Foundation weekly target'),
               ('W4','1900-01-01',30,'Initial Foundation weekly target'),
               ('Steady State','1900-01-01',30,'Initial Foundation steady-state target');
    """)


def downgrade():
    op.drop_table("foundation_target_rules")
    op.drop_table("user_stage_evidence")
    op.drop_column("users", "join_date")
