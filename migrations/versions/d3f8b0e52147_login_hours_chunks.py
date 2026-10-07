"""Retry-safe attendance chunks and persisted unmatched names."""
from alembic import op
import sqlalchemy as sa
revision = "d3f8b0e52147"
down_revision = "c2e7a9d41036"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("login_hours_upload_batches", sa.Column("upload_token", sa.String(36), nullable=True))
    op.add_column("login_hours_upload_batches", sa.Column("chunk_hashes", sa.JSON(), nullable=True))
    op.add_column("login_hours_upload_batches", sa.Column("unmatched_names", sa.JSON(), nullable=True))

def downgrade():
    for column in ("unmatched_names", "chunk_hashes", "upload_token"):
        op.drop_column("login_hours_upload_batches", column)
