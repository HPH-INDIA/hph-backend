"""The personal daily table reads saved manual CPD rather than a live target."""
import datetime as dt
from decimal import Decimal

import pytest

from app.cohorts.models import StageTargetRule, UserStagePeriod
from app.extensions import db
from app.manual_daily_records import productivity
from app.manual_daily_records.models import ManualDailyRecord
from app.manual_daily_records.services import upsert_own_record
from app.reports.services import get_efficiency


@pytest.mark.parametrize("meetings,expected", [
    ([{"type": "Huddle", "hours": Decimal("1.00")}], "30.00"),
    ([{"type": "One-O-One", "hours": Decimal("1.00")}], "26.25"),
    ([{"type": "Huddle", "hours": Decimal("0.75")},
      {"type": "One-O-One", "hours": Decimal("0.50")}], "28.13"),
])
def test_daily_api_returns_saved_cpd_after_target_changes(
    employee_user, api_client, monkeypatch, meetings, expected,
):
    day = dt.date(2026, 9, 10)
    db.session.add(UserStagePeriod(user_id=employee_user.id, stage_code="Steady State",
                                   start_date=day, source="manual_override"))
    db.session.commit()
    record = upsert_own_record(employee_user.id, dict(
        record_date=day, production_count=20, tech_issues_downtime_hours=0,
        no_inventory_idle_time_hours=0, leave_hours=0,
        meeting_engagement_hours=sum(meeting["hours"] for meeting in meetings), meetings=meetings,
    ))
    assert record.adjusted_cpd == Decimal(expected)
    rule = StageTargetRule.query.filter_by(stage_code="Steady State").one()
    rule.daily_target = 40
    # A legacy row without a saved snapshot must display no adjusted CPD.
    db.session.add(ManualDailyRecord(
        user_id=employee_user.id, record_date=day + dt.timedelta(days=1),
        production_count=0, tech_issues_downtime_hours=0, no_inventory_idle_time_hours=0,
        leave_hours=0, meeting_engagement_hours=0, status="pending",
    ))
    db.session.commit()

    def unexpected(*args, **kwargs):
        pytest.fail("Fetching the daily table recalculated saved CPD")

    monkeypatch.setattr(productivity, "adjusted_cpd", unexpected)
    api_client.login(employee_user.email, "test-password")
    status, response = api_client.get("/api/dashboards/my-efficiency?month=2026-09")
    assert status == 200, response
    rows = {row["date"]: row for row in response["data"]["daily"]}
    assert rows[day.isoformat()]["adjustedCpd"] == expected
    assert rows["2026-09-11"]["adjustedCpd"] is None
    assert rows[day.isoformat()]["manualCharts"] == 20
    db.session.refresh(record)
    assert record.adjusted_cpd == Decimal(expected)


def test_daily_saved_cpd_is_unavailable_without_configured_target(employee_user):
    day = dt.date(2026, 9, 10)
    upsert_own_record(employee_user.id, dict(
        record_date=day, production_count=20, tech_issues_downtime_hours=0,
        no_inventory_idle_time_hours=0, leave_hours=0, meeting_engagement_hours=0,
    ))
    result = get_efficiency([employee_user.id], day, day, include_daily=True)
    assert result[employee_user.id]["daily"][0]["adjusted_cpd"] is None
