"""Add Client role type and menu permissions

Revision ID: fa3d6c01490c
Revises: ca42d801ef76
Create Date: 2026-10-12 18:56:47.885774

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'fa3d6c01490c'
down_revision = 'ca42d801ef76'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("INSERT INTO role_types (code, label, hierarchy_rank, session_timeout_minutes) VALUES ('client', 'Client', 5, 30)")
    connection = op.get_bind()
    titles = {"user_management": "Users", "role_management": "Roles & Features", "project_management": "Projects", "stage_view": "Stages"}
    for codename, title in titles.items():
        connection.execute(sa.text("UPDATE features SET title = :title WHERE codename = :codename"), dict(title=title, codename=codename))
    seeds = [
        ("team", "Team", "Stages, lead coverage, team assignments, and cohorts. Actions remain limited by role type.",
         "rt.code IN ('manager', 'lead') AND EXISTS (SELECT 1 FROM role_features rf JOIN features f ON f.id = rf.feature_id WHERE rf.role_id = r.id AND f.codename IN ('user_management', 'reports', 'stage_view') AND (rf.can_read OR rf.can_write))",
         "rt.code = 'manager' AND EXISTS (SELECT 1 FROM role_features rf JOIN features f ON f.id = rf.feature_id WHERE rf.role_id = r.id AND f.codename = 'user_management' AND rf.can_write)"),
        ("chart_holds", "Chart holds", "Read current chart holds within your authorized team or project.",
         "rt.code IN ('manager', 'lead', 'employee') AND EXISTS (SELECT 1 FROM role_features rf JOIN features f ON f.id = rf.feature_id WHERE rf.role_id = r.id AND f.codename = 'reports' AND (rf.can_read OR rf.can_write))", "false"),
        ("help_guides", "Help & guides", "Read guides for workflows available to your role.", "true", "false"),
    ]
    for codename, title, description, readable, writable in seeds:
        feature_id = connection.execute(sa.text("INSERT INTO features (codename, title, description, active) VALUES (:code, :title, :description, true) RETURNING id"), dict(code=codename, title=title, description=description)).scalar_one()
        connection.execute(sa.text(f"INSERT INTO role_features (role_id, feature_id, can_read, can_write) SELECT r.id, :feature_id, true, {writable} FROM roles r JOIN role_types rt ON rt.id = r.role_type_id WHERE {readable}"), dict(feature_id=feature_id))


def downgrade():
    connection = op.get_bind()
    connection.execute(sa.text("DELETE FROM role_features WHERE feature_id IN (SELECT id FROM features WHERE codename IN ('team', 'chart_holds', 'help_guides'))"))
    connection.execute(sa.text("DELETE FROM features WHERE codename IN ('team', 'chart_holds', 'help_guides')"))
    # RESTRICT prevents removal while Client roles are still assigned.
    connection.execute(sa.text("DELETE FROM role_types WHERE code = 'client'"))
    for code, title in {"user_management": "User Management", "role_management": "Role Management", "project_management": "Project Management", "stage_view": "View stages"}.items():
        connection.execute(sa.text("UPDATE features SET title = :title WHERE codename = :code"), dict(title=title, code=code))
