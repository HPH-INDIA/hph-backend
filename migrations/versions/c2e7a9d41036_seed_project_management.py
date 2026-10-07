"""Seed project management with configurable read/write role permissions."""
from alembic import op
import sqlalchemy as sa
revision = "c2e7a9d41036"
down_revision = "c6f1a9d2e803"
branch_labels = None
depends_on = None

def upgrade():
    op.create_index("uq_projects_name_lower", "projects", [sa.text("lower(name)")], unique=True)
    connection = op.get_bind()
    feature_id = connection.execute(sa.text("INSERT INTO features (codename, title, description, active) VALUES ('project_management', 'Project Management', 'Read projects or create, rename, and delete unused custom projects with write access.', true) RETURNING id")).scalar_one()
    connection.execute(sa.text("INSERT INTO role_features (role_id, feature_id, can_read, can_write) SELECT r.id, :feature_id, true, true FROM roles r JOIN role_types rt ON rt.id = r.role_type_id WHERE rt.code IN ('super_admin', 'admin')"), {"feature_id": feature_id})

def downgrade():
    op.drop_index("uq_projects_name_lower", table_name="projects")
    connection = op.get_bind()
    connection.execute(sa.text("DELETE FROM role_features WHERE feature_id IN (SELECT id FROM features WHERE codename = 'project_management')"))
    connection.execute(sa.text("DELETE FROM features WHERE codename = 'project_management'"))
