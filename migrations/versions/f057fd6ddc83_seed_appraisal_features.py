"""seed appraisal features

Revision ID: f057fd6ddc83
Revises: b8f6e02d3c41
Create Date: 2026-10-03 19:38:21.210978

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f057fd6ddc83'
down_revision = 'b8f6e02d3c41'
branch_labels = None
depends_on = None

features_table = sa.table(
    "features",
    sa.column("id", sa.Integer),
    sa.column("codename", sa.String),
    sa.column("title", sa.String),
    sa.column("description", sa.Text),
    sa.column("active", sa.Boolean),
)

roles_table = sa.table(
    "roles",
    sa.column("id", sa.Integer),
    sa.column("title", sa.String),
)

role_features_table = sa.table(
    "role_features",
    sa.column("role_id", sa.Integer),
    sa.column("feature_id", sa.Integer),
)

# Read by the appraisal and form builder services through the gateway's signed user token.
# Admins run cycles and forms and monitor; Leads and Managers review; Employees and Leads are
# appraised. Granted to the starter roles seeded by 2315bc38cac3 only: roles created later in
# the UI (e.g. "IT admin") get them through POST /api/roles/{id}/features if needed.
FEATURES = (
    (
        "form_builder",
        "Appraisal Form Builder",
        "Design, publish and assign appraisal forms.",
        ("Super Admin", "Admin"),
    ),
    (
        "appraisal_cycle_admin",
        "Appraisal Cycle Administration",
        "Create, launch and close appraisal cycles, reassign reviewers and monitor progress.",
        ("Super Admin", "Admin"),
    ),
    (
        "appraisal_review",
        "Appraisal Review",
        "Review the appraisals of the people who report to you.",
        ("Manager", "Lead"),
    ),
    (
        "appraisal_self",
        "My Appraisal",
        "Fill in and submit your own appraisal.",
        ("Employee", "Lead"),
    ),
)


def upgrade():
    connection = op.get_bind()

    for codename, title, description, role_titles in FEATURES:
        feature_id = connection.execute(
            features_table.insert()
            .values(codename=codename, title=title, description=description, active=True)
            .returning(features_table.c.id)
        ).scalar_one()

        role_ids = [
            row[0]
            for row in connection.execute(
                sa.select(roles_table.c.id).where(roles_table.c.title.in_(role_titles))
            )
        ]
        if role_ids:
            op.bulk_insert(
                role_features_table,
                [{"role_id": role_id, "feature_id": feature_id} for role_id in role_ids],
            )


def downgrade():
    connection = op.get_bind()
    codenames = [codename for codename, _, _, _ in FEATURES]

    feature_ids = [
        row[0]
        for row in connection.execute(
            sa.select(features_table.c.id).where(features_table.c.codename.in_(codenames))
        )
    ]
    if feature_ids:
        connection.execute(
            role_features_table.delete().where(role_features_table.c.feature_id.in_(feature_ids))
        )
        connection.execute(features_table.delete().where(features_table.c.id.in_(feature_ids)))
