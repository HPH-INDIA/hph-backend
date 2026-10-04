"""Check SQL backfill against the manual calculation, without altering real tables."""
import datetime as dt
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.extensions import db
from app.manual_daily_records.productivity import adjusted_cpd


def test_backfill_uses_manual_meetings_and_date_targets(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "migrations/versions/c6f1a9d2e803_persist_manual_adjusted_cpd.py"
    spec = importlib.util.spec_from_file_location("cpd_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    variants = [
        (None, "One-O-One", 1),
        (None, "Huddle", 1),
        (None, None, 1),
        ([{"type": "Huddle", "hours": "1"}, {"type": "One-O-One", "hours": "0.5"}], None, 1.5),
        ([], None, 0),
    ]
    with db.engine.begin() as connection:
        # Session-local tables shadow the application tables for this connection.
        connection.execute(text("""CREATE TEMP TABLE manual_daily_records (
            id integer PRIMARY KEY, user_id integer, record_date date,
            tech_issues_downtime_hours numeric, no_inventory_idle_time_hours numeric,
            leave_hours numeric, meeting_engagement_hours numeric,
            meeting_type text, meetings json
        ) ON COMMIT DROP"""))
        connection.execute(text("""CREATE TEMP TABLE user_stage_periods (
            user_id integer, stage_code text, start_date date, end_date date
        ) ON COMMIT DROP"""))
        connection.execute(text("""CREATE TEMP TABLE stage_target_rules (
            stage_code text, daily_target integer, effective_from date, effective_to date
        ) ON COMMIT DROP"""))
        connection.execute(text("""INSERT INTO user_stage_periods VALUES
            (1, 'Steady State', '2026-09-01', '2026-09-10')"""))
        connection.execute(text("""INSERT INTO stage_target_rules VALUES
            ('Steady State', 30, '1900-01-01', '2026-09-10'),
            ('Steady State', 40, '2026-09-10', NULL)"""))
        expected = {}
        for index, (meetings, meeting_type, meeting_hours) in enumerate(variants, 1):
            for day, target in [(9, 30), (10, 40), (11, None)]:
                record_id = index * 100 + day
                values = dict(id=record_id, user_id=1, record_date=dt.date(2026, 9, day),
                              tech_issues_downtime_hours=1, no_inventory_idle_time_hours=0.5,
                              leave_hours=0.5, meeting_engagement_hours=meeting_hours,
                              meeting_type=meeting_type, meetings=meetings)
                expected[record_id] = (target, adjusted_cpd(SimpleNamespace(**values), target))
                values["meetings"] = json.dumps(meetings)
                connection.execute(text("""INSERT INTO manual_daily_records VALUES (
                    :id, :user_id, :record_date, :tech_issues_downtime_hours,
                    :no_inventory_idle_time_hours, :leave_hours, :meeting_engagement_hours,
                    :meeting_type, CAST(:meetings AS json))"""), values)
        # Cover SQL NULL as well as JSON null.
        connection.execute(text("UPDATE manual_daily_records SET meetings = NULL WHERE id = 109"))
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        actual = {row.id: (row.daily_target, row.adjusted_cpd) for row in connection.execute(
            text("SELECT id, daily_target, adjusted_cpd FROM manual_daily_records"))}
        assert actual == expected
        migration.downgrade()
        assert connection.execute(text("""SELECT count(*) FROM pg_attribute
            WHERE attrelid = 'pg_temp.manual_daily_records'::regclass
              AND attname IN ('daily_target', 'adjusted_cpd') AND NOT attisdropped""")).scalar() == 0
