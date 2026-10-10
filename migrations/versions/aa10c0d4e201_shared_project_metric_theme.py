"""Persist shared project metric themes.

Revision ID: aa10c0d4e201
Revises: 44a1e2e48e73
"""
from alembic import op
import sqlalchemy as sa

revision = "aa10c0d4e201"
down_revision = "44a1e2e48e73"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("projects", sa.Column("metric_theme", sa.JSON(), nullable=True))
    op.add_column("projects", sa.Column("metric_theme_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("projects", sa.Column("metric_theme_updated_by_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_projects_metric_theme_updated_by", "projects", "users", ["metric_theme_updated_by_id"], ["id"], ondelete="SET NULL")

def downgrade():
    op.drop_constraint("fk_projects_metric_theme_updated_by", "projects", type_="foreignkey")
    op.drop_column("projects", "metric_theme_updated_by_id")
    op.drop_column("projects", "metric_theme_updated_at")
    op.drop_column("projects", "metric_theme")
