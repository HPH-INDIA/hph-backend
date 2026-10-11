"""Operational refresh/backfill for changes to stage business rules."""
import click
from flask.cli import with_appcontext
from sqlalchemy import text

from app.extensions import db


@click.command('refresh-user-stages')
@click.option('--recalculate-targets', is_flag=True, help='Recalculate saved manual target snapshots using the rebuilt stages.')
@with_appcontext
def refresh_user_stages_command(recalculate_targets):
    from app.cohorts.stage_refresh import coding_users_query, refresh_all_user_stages
    from app.cohorts.stage_changes import recalculate_user_records
    from datetime import date
    try:
        # Same lock order as stage changes and target changes.
        db.session.execute(text('SELECT pg_advisory_xact_lock(740821, 1)'))
        if recalculate_targets:
            db.session.execute(text('SELECT pg_advisory_xact_lock(740821, 2)'))
            db.session.execute(text('LOCK TABLE manual_daily_records IN SHARE ROW EXCLUSIVE MODE'))
        users = refresh_all_user_stages()
        records = 0
        if recalculate_targets:
            for user in coding_users_query().order_by(db.text('users.id')).all():
                records += recalculate_user_records(user.id, date.min, user.last_working_day)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    click.echo(f'Refreshed {users} user stage timelines; recalculated {records} saved manual target records.')
