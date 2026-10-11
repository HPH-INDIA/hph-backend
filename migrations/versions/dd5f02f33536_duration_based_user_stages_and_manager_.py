"""Duration based user stages and manager restarts.

Revision ID: dd5f02f33536
Revises: aa10c0d4e201
"""
from alembic import op
import sqlalchemy as sa

revision = 'dd5f02f33536'
down_revision = 'aa10c0d4e201'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'user_stage_changes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('program', sa.String(16), nullable=False),
        sa.Column('stage_code', sa.String(32), nullable=True),
        sa.Column('effective_from', sa.Date(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('recalculated_records', sa.Integer(), nullable=False, server_default='0'),
        sa.CheckConstraint("program IN ('main','foundation')", name='ck_user_stage_change_program'),
        sa.CheckConstraint("stage_code IS NULL OR (program = 'main' AND stage_code IN ('M1','M2','M3','M4','Steady State')) OR (program = 'foundation' AND stage_code IN ('W1','W2','W3','W4','Steady State'))", name='ck_user_stage_change_stage'),
        sa.CheckConstraint('length(trim(reason)) > 0', name='ck_user_stage_change_reason'),
        sa.CheckConstraint('recalculated_records >= 0', name='ck_user_stage_change_records'),
    )
    op.create_index('ix_user_stage_changes_timeline', 'user_stage_changes', ['user_id', 'program', 'effective_from', 'id'])
    op.create_index('ix_user_stage_changes_created_by_id', 'user_stage_changes', ['created_by_id'])
    # The server's database role owns the tables; all access is authenticated
    # and scoped by the Flask API. No public Data API policies are granted.
    op.execute('ALTER TABLE user_stage_changes ENABLE ROW LEVEL SECURITY')
    op.drop_constraint('uq_user_stage_periods_user_stage', 'user_stage_periods', type_='unique')
    # Rebuild the operational users' cache. Source evidence and saved manual
    # snapshots are retained; the rollout command recalculates snapshots once.
    op.execute("""
        DELETE FROM user_stage_periods WHERE user_id IN (
            SELECT u.id FROM users u JOIN roles r ON r.id=u.role_id
            JOIN role_types rt ON rt.id=r.role_type_id JOIN projects p ON p.id=u.project_id
            WHERE p.name='CODING' AND rt.code IN ('lead','employee')
        );
        WITH people AS (
            SELECT u.id, coalesce(u.join_date, cm.joined_on) AS joined, u.last_working_day
            FROM users u JOIN roles r ON r.id=u.role_id
            JOIN role_types rt ON rt.id=r.role_type_id JOIN projects p ON p.id=u.project_id
            LEFT JOIN cohort_memberships cm ON cm.user_id=u.id
            WHERE p.name='CODING' AND rt.code IN ('lead','employee')
        ), path(code, start_offset, end_offset) AS (
            VALUES ('Training',0,13),('M1',14,43),('M2',44,73),
                   ('M3',74,103),('M4',104,133),('Steady State',134,NULL::integer)
        )
        INSERT INTO user_stage_periods(user_id,stage_code,start_date,end_date,source,shifted_by_exception_days)
        SELECT people.id,path.code,joined+start_offset,
               CASE WHEN last_working_day IS NULL THEN joined+end_offset
                    ELSE least(coalesce(joined+end_offset,last_working_day),last_working_day) END,
               'calendar_offset',0
        FROM people CROSS JOIN path WHERE joined IS NOT NULL
          AND (last_working_day IS NULL OR joined+start_offset <= last_working_day);
        ALTER TABLE user_stage_periods ADD CONSTRAINT ck_user_stage_periods_range
            CHECK (end_date IS NULL OR end_date >= start_date);
        ALTER TABLE user_stage_periods ADD CONSTRAINT ex_user_stage_periods_no_overlap
            EXCLUDE USING gist (user_id WITH =, daterange(start_date,end_date,'[]') WITH &&);
    """)


def downgrade():
    # A rollback must not discard recorded manager restarts silently.
    count = op.get_bind().execute(sa.text('SELECT count(*) FROM user_stage_changes')).scalar()
    if count:
        raise RuntimeError('Cannot downgrade while manager stage-change history exists.')
    op.drop_constraint('ex_user_stage_periods_no_overlap', 'user_stage_periods')
    op.drop_constraint('ck_user_stage_periods_range', 'user_stage_periods')
    op.create_unique_constraint('uq_user_stage_periods_user_stage', 'user_stage_periods', ['user_id','stage_code'])
    op.drop_table('user_stage_changes')
