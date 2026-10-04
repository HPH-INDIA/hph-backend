"""Calendar goal reductions use saved manual CPD, including the supplied example."""
import datetime as dt
from decimal import Decimal
from uuid import uuid4

import pytest

from app.cohorts.models import StageTargetRule, UserStagePeriod
from app.encryption.passwords import hash_password
from app.extensions import db
from app.manual_daily_records import productivity
from app.manual_daily_records.models import ManualDailyRecord
from app.manual_daily_records.services import approve_record, reject_record, upsert_own_record
from app.reports.models import OfficeHoliday
from app.reports.services import get_monthly_goal
from app.roles.models import Role, RoleType
from app.users.models import User


def _steady(user, start=dt.date(2026, 9, 1)):
    db.session.add(UserStagePeriod(user_id=user.id, stage_code="Steady State",
                                   start_date=start, source="manual_override"))
    db.session.commit()


def _entry(day, **overrides):
    values = dict(record_date=dt.date(2026, 9, day), production_count=20,
                  tech_issues_downtime_hours=0, no_inventory_idle_time_hours=0,
                  leave_hours=0, meeting_engagement_hours=Decimal("0.42"), meeting_type="Huddle")
    values.update(overrides)
    return values


def _example(user):
    _steady(user)
    first = upsert_own_record(user.id, _entry(10))
    second = upsert_own_record(user.id, _entry(11, production_count=25,
                                               tech_issues_downtime_hours=1,
                                               meeting_engagement_hours=Decimal("0.25")))
    assert first.adjusted_cpd == Decimal("30.00")
    assert second.adjusted_cpd == Decimal("26.25")
    return first, second


def test_note_example_returns_626_25_target_and_581_25_left(employee_user, api_client, monkeypatch):
    first, second = _example(employee_user)

    def unexpected(*args, **kwargs):
        pytest.fail("Monthly goal must not recalculate saved CPD")

    monkeypatch.setattr(productivity, "adjusted_cpd", unexpected)
    result = get_monthly_goal(employee_user, "2026-09")
    assert result["target_charts"] == 630
    assert result["adjusted_target_charts"] == Decimal("626.25")
    assert result["manual_charts"] == 45
    assert result["adjusted_difference"] == Decimal("581.25")
    assert result["users"][0]["adjusted_target_charts"] == Decimal("626.25")
    assert result["users"][0]["adjusted_difference"] == Decimal("581.25")

    api_client.login(employee_user.email, "test-password")
    status, response = api_client.get("/api/dashboards/monthly-goal?month=2026-09")
    assert status == 200, response
    assert response["data"]["scope"] == "self"
    assert response["data"]["adjustedTargetCharts"] == "626.25"
    assert response["data"]["adjustedDifference"] == "581.25"
    assert response["data"]["users"][0]["adjustedTargetCharts"] == "626.25"
    assert first.adjusted_cpd == Decimal("30.00")
    assert second.adjusted_cpd == Decimal("26.25")


def test_adjustments_accumulate_but_full_leave_is_not_deducted_twice(employee_user):
    _example(employee_user)
    upsert_own_record(employee_user.id, _entry(16, production_count=5, leave_hours=2,
                                               meeting_type="One-O-One", meeting_engagement_hours=1))
    upsert_own_record(employee_user.id, _entry(15, production_count=0, leave_hours=8))
    result = get_monthly_goal(employee_user, "2026-09")
    assert result["leave_days_excluded"] == 1
    assert result["target_charts"] == 600
    assert result["adjusted_target_charts"] == Decimal("585.00")  # 600 - 3.75 - 11.25
    assert result["adjusted_difference"] == Decimal("535.00")


def test_rejected_adjustments_do_not_change_the_goal(employee_user, superadmin):
    first, second = _example(employee_user)
    approve_record(first, superadmin.id)
    reject_record(second, superadmin.id, "Wrong hours")
    db.session.commit()
    result = get_monthly_goal(employee_user, "2026-09")
    assert result["adjusted_target_charts"] == Decimal("630.00")
    assert result["adjusted_difference"] == Decimal("610.00")


def test_weekends_holidays_and_outside_month_are_not_adjusted(employee_user):
    _steady(employee_user)
    weekday_holiday = OfficeHoliday.query.filter(
        OfficeHoliday.holiday_date >= dt.date(2026, 9, 1),
        OfficeHoliday.holiday_date <= dt.date(2026, 9, 30),
    ).first().holiday_date
    for day in [dt.date(2026, 9, 12), weekday_holiday, dt.date(2026, 8, 31)]:
        upsert_own_record(employee_user.id, _entry(10, record_date=day,
                                                   production_count=0, tech_issues_downtime_hours=8))
    result = get_monthly_goal(employee_user, "2026-09")
    assert result["target_charts"] == 630
    assert result["adjusted_target_charts"] == Decimal("630.00")


def test_target_rule_change_uses_saved_reduction_and_missing_snapshots_are_not_guessed(employee_user):
    _, second = _example(employee_user)
    rule = StageTargetRule.query.filter_by(stage_code="Steady State").one()
    rule.daily_target = 40
    db.session.add(ManualDailyRecord(user_id=employee_user.id, record_date=dt.date(2026, 9, 16),
                                    production_count=0, tech_issues_downtime_hours=8,
                                    no_inventory_idle_time_hours=0, leave_hours=0,
                                    meeting_engagement_hours=0, status="pending"))
    db.session.commit()
    result = get_monthly_goal(employee_user, "2026-09")
    assert result["target_charts"] == 840
    assert result["adjusted_target_charts"] == Decimal("836.25")
    assert second.daily_target == 30
    assert second.adjusted_cpd == Decimal("26.25")


def test_stage_transition_uses_the_daily_stage_target(employee_user):
    db.session.add(UserStagePeriod(user_id=employee_user.id, stage_code="M1",
                                   start_date=dt.date(2026, 9, 1), end_date=dt.date(2026, 9, 10),
                                   source="manual_override"))
    db.session.commit()
    _steady(employee_user, dt.date(2026, 9, 11))
    upsert_own_record(employee_user.id, _entry(10, production_count=0, tech_issues_downtime_hours=4))
    upsert_own_record(employee_user.id, _entry(11, production_count=0, tech_issues_downtime_hours=1))
    result = get_monthly_goal(employee_user, "2026-09")
    assert result["target_charts"] == 446  # eight M1 days, thirteen Steady State days
    assert result["adjusted_target_charts"] == Decimal("438.75")


def test_lead_goal_sums_individual_adjusted_goals(employee_user, manager_user):
    token = uuid4().hex[:12]
    role = Role.query.join(RoleType).filter(RoleType.code == "lead").first()
    lead = User(email=f"adjusted-goal-{token}@example.com", first_name="Adjusted", last_name="Lead",
                emp_id=f"AG-{token}", role_id=role.id, project_id=manager_user.project_id,
                reports_to_id=manager_user.id, password_hash=hash_password("test-password"),
                first_login=False, is_active=True)
    db.session.add(lead)
    db.session.flush()
    previous_lead_id = employee_user.reports_to_id
    employee_user.reports_to_id = lead.id
    db.session.commit()
    try:
        _example(employee_user)
        _steady(lead)
        upsert_own_record(lead.id, _entry(10, production_count=10, no_inventory_idle_time_hours=2))
        result = get_monthly_goal(lead, "2026-09")
        assert result["user_count"] == 2
        assert result["target_charts"] == 1260
        assert result["adjusted_target_charts"] == Decimal("1248.75")
        assert result["adjusted_difference"] == Decimal("1193.75")
        assert result["adjusted_target_charts"] == sum(row["adjusted_target_charts"] for row in result["users"])
    finally:
        employee_user.reports_to_id = previous_lead_id
        db.session.commit()
