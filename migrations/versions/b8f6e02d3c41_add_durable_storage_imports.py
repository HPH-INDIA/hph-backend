"""Durable signed-storage imports and one active slot per type.

Revision ID: b8f6e02d3c41
Revises: a7e5d91c2b30
"""
from alembic import op
import sqlalchemy as sa

revision = "b8f6e02d3c41"
down_revision = "a7e5d91c2b30"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("storage_imports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("uploaded_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("source_filename", sa.String(255), nullable=False),
        sa.Column("source_checksum", sa.String(64), nullable=False),
        sa.Column("object_path", sa.String(255), nullable=False, unique=True),
        sa.Column("wrapped_key", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="uploading"),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("file_size", sa.Integer()),
        sa.Column("file_checksum", sa.String(64)),
        sa.Column("batch_id", sa.Integer()),
        sa.Column("error", sa.String(500)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("uploaded_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_storage_imports_status", "storage_imports", ["status"])
    slots = op.create_table("storage_import_slots",
        sa.Column("kind", sa.String(16), primary_key=True),
        sa.Column("import_id", sa.String(36), sa.ForeignKey("storage_imports.id")),
    )
    op.bulk_insert(slots, [{"kind": "kairon"}, {"kind": "manual"}])
    op.execute("ALTER TABLE storage_imports ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE storage_import_slots ENABLE ROW LEVEL SECURITY")
    # Match existing migrations' backend runtime grants. No Data API grants.
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hph_app') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON storage_imports, storage_import_slots TO hph_app;
            CREATE POLICY storage_imports_backend ON storage_imports TO hph_app USING (true) WITH CHECK (true);
            CREATE POLICY storage_import_slots_backend ON storage_import_slots TO hph_app USING (true) WITH CHECK (true);
        END IF;
    END $$""")


def downgrade():
    op.drop_table("storage_import_slots")
    op.drop_table("storage_imports")
