"""Saved CPD survives reads/reviews and changes only on manual submissions."""
import base64
import datetime as dt
from decimal import Decimal
from io import BytesIO
from uuid import uuid4

import pytest
from openpyxl import Workbook
from sqlalchemy import event

from app.cohorts.models import Cohort, CohortMembership, StageTargetRule, UserStagePeriod
from app.encryption.passwords import hash_password
from app.extensions import db
from app.manual_daily_records import productivity
from app.manual_daily_records.models import ManualDailyRecord
from app.manual_daily_records.services import (
    MANUAL_UPLOAD_HEADERS, approve_record, import_manual_daily_records,
    process_manual_import_chunk, reject_record, start_manual_import, upsert_own_record,
)
from app.roles.models import Role, RoleType
from app.users.models import User

DAY = dt.date(2026, 9, 10)


def _period(user, stage="Steady State", start=DAY, end=None):
    period = UserStagePeriod(user_id=user.id, stage_code=stage, start_date=start,
                             end_date=end, source="manual_override")
    db.session.add(period)
    db.session.commit()
    return period


def _entry(**overrides):
    values = dict(record_date=DAY, production_count=12,
                  tech_issues_downtime_hours=1, no_inventory_idle_time_hours=0.5,
                  leave_hours=0.5, meeting_engagement_hours=1, meeting_type="One-O-One")
    values.update(overrides)
    return values


def test_create_fetch_review_and_edit_saved_cpd(api_client, employee_user, superadmin, monkeypatch):
    _period(employee_user)
    api_client.login(employee_user.email, "test-password")
    body = dict(date=DAY.isoformat(), productionCount=12, techIssuesDowntimeHours=1,
                noInventoryIdleTimeHours=0.5, leaveHours=0.5, meetingEngagementHours=1.25,
                meetings=[{"type": "One-O-One", "hours": 0.5}, {"type": "Huddle", "hours": 0.75}])
    status, response = api_client.post("/api/manual-daily-records", body)
    assert status == 200, response
    saved = response["data"]
    assert saved["dailyTarget"] == 30
    assert saved["adjustedCpd"] == "20.63"
    rule = StageTargetRule.query.filter_by(stage_code="Steady State").one()
    rule.daily_target = 40
    db.session.commit()
    db.session.expire_all()

    def unexpected_calculation(*args, **kwargs):
        pytest.fail("A fetch or review recalculated CPD")

    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    with monkeypatch.context() as patch:
        patch.setattr(productivity, "adjusted_cpd", unexpected_calculation)
        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            status, response = api_client.get("/api/manual-daily-records")
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)
        assert status == 200, response
        assert response["data"][0]["dailyTarget"] == 30
        assert response["data"][0]["adjustedCpd"] == "20.63"
        assert not any("user_stage_periods" in query or "stage_target_rules" in query
                       for query in statements)
        record = db.session.get(ManualDailyRecord, saved["id"])
        approve_record(record, superadmin.id)
        db.session.commit()
        reject_record(record, superadmin.id, "Please update")
        db.session.commit()
        assert record.daily_target == 30
        assert record.adjusted_cpd == Decimal("20.63")

    body.update(meetingEngagementHours=2, meetings=[{"type": "One-O-One", "hours": 2}])
    status, response = api_client.post("/api/manual-daily-records", body)
    assert status == 200, response
    assert response["data"]["id"] == saved["id"]
    assert response["data"]["dailyTarget"] == 40
    assert response["data"]["adjustedCpd"] == "20.00"
    db.session.expire_all()
    record = db.session.get(ManualDailyRecord, saved["id"])
    assert record.adjusted_cpd == Decimal("20.00")


def test_snapshot_uses_record_date_stage_and_rule_boundaries(employee_user, superadmin):
    _period(employee_user, "M1", DAY - dt.timedelta(days=10), DAY - dt.timedelta(days=1))
    _period(employee_user)
    old_rule = StageTargetRule.query.filter_by(stage_code="Steady State").one()
    old_rule.effective_to = DAY
    db.session.flush()
    db.session.add(StageTargetRule(stage_code="Steady State", effective_from=DAY,
                                   daily_target=40, created_by_id=superadmin.id))
    db.session.commit()
    prior = upsert_own_record(employee_user.id, _entry(record_date=DAY - dt.timedelta(days=1)))
    current = upsert_own_record(employee_user.id, _entry())
    assert prior.daily_target == 7
    assert prior.adjusted_cpd == Decimal("4.38")
    assert current.daily_target == 40
    assert current.adjusted_cpd == Decimal("25.00")


def _membership(user, assigned_by):
    cohort = Cohort(sequence_no=999, label="CPD test", window_start=DAY - dt.timedelta(days=1))
    db.session.add(cohort)
    db.session.flush()
    db.session.add(CohortMembership(cohort_id=cohort.id, user_id=user.id,
                                    joined_on=cohort.window_start, assigned_by_id=assigned_by.id))
    db.session.commit()
    _period(user, "Training", cohort.window_start)


def test_manual_production_without_kairon_keeps_training(employee_user, superadmin):
    _membership(employee_user, superadmin)
    record = upsert_own_record(employee_user.id, _entry())
    assert record.daily_target is None
    assert record.adjusted_cpd is None
    assert UserStagePeriod.query.filter_by(user_id=employee_user.id).one().stage_code == "Training"


@pytest.mark.parametrize("stage", [None, "Training"])
def test_missing_target_is_saved_as_null(employee_user, stage):
    if stage is not None:
        _period(employee_user, stage)
    record = upsert_own_record(employee_user.id, _entry())
    assert record.daily_target is None
    assert record.adjusted_cpd is None


@pytest.fixture
def import_employee(manager_user, employee_user):
    token = uuid4().hex[:12]
    role = Role.query.join(RoleType).filter(RoleType.code == "lead").first()
    lead = User(email=f"cpd-lead-{token}@example.com", first_name="CPD", last_name=token,
                emp_id=f"CPD-{token}", role_id=role.id, project_id=manager_user.project_id,
                reports_to_id=manager_user.id, password_hash=hash_password("test-password"),
                first_login=False, is_active=True)
    db.session.add(lead)
    db.session.flush()
    employee_user.reports_to_id = lead.id
    db.session.commit()
    return employee_user


@pytest.mark.parametrize("path", ["workbook", "chunk"])
@pytest.mark.parametrize("first_production", [False, True])
def test_imports_snapshot_after_stage_updates_and_preserve_unchanged_rows(
    manager_user, import_employee, superadmin, path, first_production,
):
    if first_production:
        _membership(import_employee, superadmin)
        # Production starts only after a completed Kairon chart, before the
        # manual upload whose saved CPD this test exercises.
        from app.kairon.services import import_batch
        import_batch(DAY, [dict(program="PVP", level="1LR", status="Completed",
                               coding_analyst=f"{import_employee.first_name} {import_employee.last_name}",
                               created_date=DAY, completed_date=DAY)], manager_user.id)
    else:
        _period(import_employee)

    def submit(leave, suffix):
        if path == "workbook":
            workbook = Workbook()
            workbook.active.append(MANUAL_UPLOAD_HEADERS)
            workbook.active.append([import_employee.email,
                                    f"{import_employee.first_name} {import_employee.last_name}",
                                    12, 1, 0.5, leave, 1, 0])
            output = BytesIO()
            workbook.save(output)
            import_manual_daily_records(base64.b64encode(output.getvalue()).decode(),
                                        "cpd.xlsx", DAY, manager_user.id)
            return None
        batch = start_manual_import("cpd.xlsx", suffix * 64, 1, manager_user.id)
        row = _entry(leave_hours=leave)
        row.pop("meeting_type")
        row["user_id"] = import_employee.id
        _, chunk = process_manual_import_chunk(batch.id, 0, suffix * 64, [row], manager_user.id)
        return chunk

    submit(0.5, "a")
    record = ManualDailyRecord.query.filter_by(user_id=import_employee.id, record_date=DAY).one()
    target = 7 if first_production else 30
    assert record.daily_target == target
    assert record.adjusted_cpd == Decimal("4.38" if first_production else "18.75")

    # An identical chunk is skipped even if the configured target has changed.
    rule = StageTargetRule.query.filter_by(stage_code="M1" if first_production else "Steady State").one()
    rule.daily_target = 40
    db.session.commit()
    if path == "chunk":
        chunk = submit(0.5, "b")
        assert chunk.unchanged_count == 1
        assert record.daily_target == target
    submit(1.5, "c")
    assert record.daily_target == 40
    assert record.adjusted_cpd == Decimal("20.00")
