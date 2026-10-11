"""Read only stages for leads and coders

Revision ID: f10018702a3a
Revises: dd5f02f33536
Create Date: 2026-10-11 08:39:39.888786

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'f10018702a3a'
down_revision = 'dd5f02f33536'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        INSERT INTO features(codename,title,description,active)
        VALUES ('stage_view','View stages','Read own stages; leads also read their directly assigned coders.',true);
        INSERT INTO role_features(role_id,feature_id,can_read,can_write)
        SELECT r.id,f.id,true,false FROM roles r
        JOIN role_types rt ON rt.id=r.role_type_id
        CROSS JOIN features f
        WHERE rt.code IN ('lead','employee') AND f.codename='stage_view';
    """)


def downgrade():
    op.execute("""
        DELETE FROM role_features WHERE feature_id IN (SELECT id FROM features WHERE codename='stage_view');
        DELETE FROM features WHERE codename='stage_view';
    """)
