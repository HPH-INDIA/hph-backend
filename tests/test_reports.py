import datetime as dt
from decimal import Decimal

import pytest
from werkzeug.exceptions import HTTPException

from app.encryption.passwords import hash_password
from app.cohorts.models import Cohort, CohortMembership, UserStagePeriod
from app.extensions import db
from app.kairon.models import KaironChartAnalystAction, KaironChartRecord
from app.kairon.services import import_batch
from app.login_hours.models import LoginHourRecord, LoginHoursUploadBatch
from app.manual_daily_records.services import approve_record, upsert_own_record
from app.reports.services import (
    bulk_approve_manual_records,
    bulk_reject_manual_records,
    get_efficiency,
    get_coding_dashboard,
    get_monthly_goal,
    resolve_dashboard_window,
)
from app.roles.models import Role, RoleType
from app.users.models import Project, User


def _get_or_create_user(email, first_name, last_name, emp_id, role_type_code="employee", project_name="CODING"):
    role_type = RoleType.query.filter_by(code=role_type_code).one()
    role = Role.query.filter_by(role_type_id=role_type.id).first()
    project = Project.query.filter_by(name=project_name).one() if project_name else None
    user = User.query.filter_by(email=email).first()
    if user is None:
        user = User(
            email=email,
            first_name=first_name,
            last_name=last_name,
            emp_id=emp_id,
            role_id=role.id,
            project_id=project.id if project else None,
            password_hash=hash_password("test-password"),
            first_login=False,
            is_active=True,
        )
        db.session.add(user)
        db.session.commit()
    elif user.project_id != (project.id if project else None):
        user.project_id = project.id if project else None
        db.session.commit()
    return user


def _manual_entry(**overrides):
    entry = {
        "record_date": dt.date(2026, 9, 10),
        "production_count": 10,
        "tech_issues_downtime_hours": 1,
        "no_inventory_idle_time_hours": 0.5,
        "leave_hours": 0,
        "meeting_engagement_hours": 1,
    }
    entry.update(overrides)
    return entry


def _manual_entry_body(**overrides):
    body = {
        "date": "2026-09-10",
        "productionCount": 10,
        "techIssuesDowntimeHours": 1,
        "noInventoryIdleTimeHours": 0.5,
        "leaveHours": 0,
        "meetingEngagementHours": 1,
    }
    body.update(overrides)
    return body


def _kairon_row(user, status, **overrides):
    row = {
        "program": "PVP",
        "level": "1LR",
        "status": status,
        "coding_analyst": f"{user.first_name} {user.last_name} - HPH Coding Analyst",
        "actions": 1,
        "last_action": "No findings, close out",
        "created_date": dt.date(2026, 8, 28),
        "completed_date": None,
        "tat_days": None,
        "age_days": 6,
        "practice": "Some Practice",
    }
    row.update(overrides)
    return row


def _first_manager_id():
    role_type = RoleType.query.filter_by(code="manager").one()
    role = Role.query.filter_by(role_type_id=role_type.id).first()
    return User.query.filter_by(role_id=role.id).first().id


# --------------------------------------------------------------------------
# Service-level: window resolution, bulk review, and dashboard aggregation -
# mirrors test_manual_daily_records.py's/test_kairon.py's style.
# --------------------------------------------------------------------------


def test_resolve_dashboard_window_defaults_to_month_to_date():
    today = dt.date.today()
    assert resolve_dashboard_window({}) == (today.replace(day=1), today)


def test_resolve_dashboard_window_with_explicit_date():
    target = dt.date(2026, 9, 10)
    assert resolve_dashboard_window({"date": target}) == (target, target)


def test_resolve_dashboard_window_with_explicit_range():
    from_date, to_date = dt.date(2026, 9, 1), dt.date(2026, 9, 15)
    assert resolve_dashboard_window({"from_date": from_date, "to_date": to_date}) == (from_date, to_date)


def test_resolve_dashboard_window_with_past_month_is_the_full_month():
    today = dt.date.today()
    last_day_of_prev_month = today.replace(day=1) - dt.timedelta(days=1)
    first_day_of_prev_month = last_day_of_prev_month.replace(day=1)

    window = resolve_dashboard_window({"month": f"{first_day_of_prev_month.year:04d}-{first_day_of_prev_month.month:02d}"})
    assert window == (first_day_of_prev_month, last_day_of_prev_month)


def test_resolve_dashboard_window_with_current_month_is_clipped_to_today():
    today = dt.date.today()
    window = resolve_dashboard_window({"month": f"{today.year:04d}-{today.month:02d}"})
    assert window == (today.replace(day=1), today)


def test_resolve_dashboard_window_with_year():
    assert resolve_dashboard_window({"year": 2025}) == (dt.date(2025, 1, 1), dt.date(2025, 12, 31))


def test_resolve_dashboard_window_rejects_invalid_month():
    with pytest.raises(HTTPException) as exc_info:
        resolve_dashboard_window({"month": "2026-13"})
    assert exc_info.value.code == 400



def _steady_state_kairon_history(user, uploaded_by_id):
    # A completed chart four 30-day stages before the report window makes
    # Steady State source-backed instead of relying on a manual override.
    import_batch(dt.date(2026, 5, 1),
                 [_kairon_row(user, "Completed", completed_date=dt.date(2026, 5, 1),
                              created_date=dt.date(2026, 5, 1))], uploaded_by_id=uploaded_by_id)


def test_monthly_goal_excludes_weekends_holiday_and_full_leave_day():
    user = _get_or_create_user(
        "reports-monthly-goal@example.com", "Monthly", "Goal", "TEST-RPT-MONTHLY-GOAL"
    )
    manager_id = _first_manager_id()
    db.session.add(
        UserStagePeriod(
            user_id=user.id,
            stage_code="Steady State",
            start_date=dt.date(2026, 9, 1),
            end_date=None,
            source="manual_override",
            shifted_by_exception_days=0,
        )
    )
    _steady_state_kairon_history(user, manager_id)
    import_batch(
        dt.date(2026, 9, 20),
        [
            _kairon_row(user, "Completed", completed_date=dt.date(2026, 9, 10)),
            _kairon_row(user, "Completed", completed_date=dt.date(2026, 9, 20)),
        ],
        uploaded_by_id=manager_id,
    )
    db.session.commit()

    result = get_monthly_goal(user, "2026-09")
    assert result["calendar_working_days"] == 21
    assert result["eligible_days"] == 21
    assert result["holiday_count"] == 1
    assert result["target_charts"] == 630
    assert result["completed_charts"] == 2
    assert result["difference"] == 628

    upsert_own_record(
        user.id,
        _manual_entry(
            record_date=dt.date(2026, 9, 15),
            production_count=0,
            leave_hours=8,
        ),
    )
    db.session.commit()

    result = get_monthly_goal(user, "2026-09")
    assert result["eligible_days"] == 20
    assert result["leave_days_excluded"] == 1
    assert result["target_charts"] == 600


def test_monthly_goal_for_lead_includes_lead_and_direct_reports():
    lead = _get_or_create_user(
        "reports-monthly-lead@example.com",
        "Monthly",
        "Lead",
        "TEST-RPT-MONTHLY-LEAD",
        "lead",
    )
    coder = _get_or_create_user(
        "reports-monthly-coder@example.com",
        "Monthly",
        "Coder",
        "TEST-RPT-MONTHLY-CODER",
    )
    original_reports_to = coder.reports_to_id
    try:
        coder.reports_to_id = lead.id
        db.session.add_all(
            [
                UserStagePeriod(
                    user_id=member.id,
                    stage_code="Steady State",
                    start_date=dt.date(2026, 9, 1),
                    end_date=None,
                    source="manual_override",
                    shifted_by_exception_days=0,
                )
                for member in (lead, coder)
            ]
        )
        db.session.commit()

        upsert_own_record(lead.id, _manual_entry(record_date=dt.date(2026, 9, 10), production_count=12))
        upsert_own_record(coder.id, _manual_entry(record_date=dt.date(2026, 9, 10), production_count=25))
        db.session.commit()
        result = get_monthly_goal(lead, "2026-09")
        assert result["scope"] == "team"
        assert result["user_count"] == 2
        assert result["calendar_working_days"] == 21
        assert result["eligible_days"] == 42
        assert result["target_charts"] == 1260
        assert result["manual_charts"] == 37
        rows = {row["user_id"]: row for row in result["users"]}
        assert rows[lead.id]["manual_charts"] == 12
        assert rows[coder.id]["manual_charts"] == 25
        assert sum(row["target_charts"] for row in rows.values()) == result["target_charts"]
        assert sum(row["completed_charts"] for row in rows.values()) == result["completed_charts"]
    finally:
        coder.reports_to_id = original_reports_to
        db.session.commit()


def test_efficiency_prorates_target_caps_overtime_and_caps_display(employee_user, manager_user):
    db.session.add(
        UserStagePeriod(
            user_id=employee_user.id,
            stage_code="Steady State",
            start_date=dt.date(2026, 9, 1),
            end_date=None,
            source="manual_override",
        )
    )
    batch = LoginHoursUploadBatch(
        source_filename="efficiency.xlsx",
        source_format="Employee-wise attendance",
        uploaded_by_id=manager_user.id,
        row_count=4,
        matched_count=4,
        unmatched_count=0,
    )
    db.session.add(batch)
    db.session.flush()
    inside_by_day = {1: 240, 2: 473, 3: 540, 4: 480}
    for day, inside_minutes in inside_by_day.items():
        db.session.add(
            LoginHourRecord(
                batch_id=batch.id,
                user_id=employee_user.id,
                attendance_date=dt.date(2026, 9, day),
                employee_name_raw="Test Employee",
                total_inside_minutes=inside_minutes,
            )
        )
    db.session.commit()
    _steady_state_kairon_history(employee_user, manager_user.id)

    upsert_own_record(employee_user.id, _manual_entry(record_date=dt.date(2026, 9, 1), production_count=15,
                                                       tech_issues_downtime_hours=0,
                                                       no_inventory_idle_time_hours=0,
                                                       leave_hours=4,
                                                       meeting_engagement_hours=0))
    upsert_own_record(employee_user.id, _manual_entry(record_date=dt.date(2026, 9, 2), production_count=30,
                                                       tech_issues_downtime_hours=0,
                                                       no_inventory_idle_time_hours=0,
                                                       meeting_engagement_hours=0.25))
    upsert_own_record(employee_user.id, _manual_entry(record_date=dt.date(2026, 9, 3), production_count=40,
                                                       tech_issues_downtime_hours=0,
                                                       no_inventory_idle_time_hours=0,
                                                       meeting_engagement_hours=0))
    upsert_own_record(employee_user.id, _manual_entry(record_date=dt.date(2026, 9, 4), production_count=15,
                                                       tech_issues_downtime_hours=1,
                                                       no_inventory_idle_time_hours=1,
                                                       leave_hours=1,
                                                       meeting_engagement_hours=1))
    import_batch(
        dt.date(2026, 9, 5),
        [
            _kairon_row(employee_user, "Completed", completed_date=dt.date(2026, 9, 3))
            for _ in range(40)
        ],
        uploaded_by_id=manager_user.id,
    )

    result = get_efficiency(
        [employee_user.id], dt.date(2026, 9, 1), dt.date(2026, 9, 30), include_daily=True
    )[employee_user.id]
    rows = {row["date"].day: row for row in result["daily"]}

    assert rows[1]["adjusted_target"] == Decimal("15.00")
    assert rows[1]["target_minutes"] == 240
    assert rows[1]["productive_minutes"] == 240
    assert rows[1]["manual_efficiency_percent"] == Decimal("100.0")
    assert rows[1]["manual_cpd"] == Decimal("30.0")
    assert rows[1]["target_cpd"] == Decimal("30.0")
    assert rows[2]["productive_minutes"] == 458
    assert rows[2]["target_minutes"] == 465
    assert rows[2]["adjusted_target"] == Decimal("29.06")
    assert rows[2]["manual_efficiency_percent"] == Decimal("103.2")
    assert rows[2]["manual_cpd"] == Decimal("31.0")
    assert rows[2]["target_cpd"] == Decimal("30.0")
    assert rows[3]["productive_minutes"] == 540
    assert rows[3]["target_minutes"] == 480
    assert rows[3]["manual_efficiency_percent"] == Decimal("120.0")
    assert rows[3]["manual_cpd"] == Decimal("40.0")
    assert rows[3]["kairon_charts"] == 40
    assert rows[3]["kairon_efficiency_percent"] == Decimal("120.0")
    assert rows[3]["kairon_cpd"] == Decimal("40.0")
    assert rows[4]["excluded_minutes"] == 240
    assert rows[4]["adjusted_target"] == Decimal("15.00")
    assert result["adjusted_target"] == Decimal("89.06")
    assert result["manual_charts"] == 100
    assert result["kairon_charts"] == 40
    assert result["manual_efficiency_percent"] == Decimal("112.3")
    assert result["kairon_efficiency_percent"] == Decimal("44.9")
    assert result["productive_minutes"] == 1538
    assert result["manual_cpd"] == Decimal("33.7")
    assert result["kairon_cpd"] == Decimal("13.5")
    assert result["target_cpd"] == Decimal("30.0")


def test_daily_refresh_target_does_not_require_login_hours(employee_user):
    work_date = dt.date(2026, 9, 8)
    db.session.add(
        UserStagePeriod(
            user_id=employee_user.id,
            stage_code="Steady State",
            start_date=work_date,
            end_date=None,
            source="manual_override",
        )
    )
    db.session.commit()
    upsert_own_record(
        employee_user.id,
        _manual_entry(
            record_date=work_date,
            production_count=30,
            tech_issues_downtime_hours=0,
            no_inventory_idle_time_hours=0,
            leave_hours=0,
            meeting_engagement_hours=0,
        ),
    )

    result = get_efficiency([employee_user.id], work_date, work_date, include_daily=True)[employee_user.id]
    row = result["daily"][0]

    assert row["inside_minutes"] is None
    assert row["productive_minutes"] is None
    assert row["target_minutes"] == 480
    assert row["adjusted_target"] == Decimal("30.00")
    assert row["manual_efficiency_percent"] == Decimal("100.0")
    assert row["manual_cpd"] == Decimal("30.0")
    assert row["target_cpd"] == Decimal("30.0")
    assert result["calculated_days"] == 1
    assert result["target_minutes"] == 480


def test_daily_refresh_target_includes_kairon_only_days(employee_user, manager_user):
    work_date = dt.date(2026, 9, 9)
    db.session.add(
        UserStagePeriod(
            user_id=employee_user.id,
            stage_code="Steady State",
            start_date=work_date,
            end_date=None,
            source="manual_override",
        )
    )
    db.session.commit()
    _steady_state_kairon_history(employee_user, manager_user.id)
    import_batch(
        work_date,
        [_kairon_row(employee_user, "Completed", completed_date=work_date)],
        uploaded_by_id=manager_user.id,
    )

    result = get_efficiency([employee_user.id], work_date, work_date, include_daily=True)[employee_user.id]
    row = result["daily"][0]

    assert row["inside_minutes"] is None
    assert row["manual_status"] is None
    assert row["target_minutes"] == 480
    assert row["adjusted_target"] == Decimal("30.00")
    assert row["kairon_charts"] == 1
    assert row["kairon_cpd"] == Decimal("1.0")
    assert row["target_cpd"] == Decimal("30.0")
    assert result["calculated_days"] == 1


def test_my_efficiency_endpoint_is_self_scoped(api_client, employee_user, manager_user):
    db.session.add(
        UserStagePeriod(
            user_id=employee_user.id,
            stage_code="Steady State",
            start_date=dt.date(2026, 9, 1),
            end_date=None,
            source="manual_override",
        )
    )
    batch = LoginHoursUploadBatch(
        source_filename="self.xlsx",
        source_format="Employee-wise attendance",
        uploaded_by_id=manager_user.id,
        row_count=1,
        matched_count=1,
        unmatched_count=0,
    )
    db.session.add(batch)
    db.session.flush()
    db.session.add(
        LoginHourRecord(
            batch_id=batch.id,
            user_id=employee_user.id,
            attendance_date=dt.date(2026, 9, 8),
            employee_name_raw="Test Employee",
            total_inside_minutes=480,
        )
    )
    db.session.commit()
    _steady_state_kairon_history(employee_user, manager_user.id)
    upsert_own_record(
        employee_user.id,
        _manual_entry(
            record_date=dt.date(2026, 9, 8),
            production_count=30,
            tech_issues_downtime_hours=0,
            no_inventory_idle_time_hours=0,
            leave_hours=0,
            meeting_engagement_hours=0,
        ),
    )
    import_batch(
        dt.date(2026, 9, 8),
        [
            _kairon_row(employee_user, "Completed", completed_date=dt.date(2026, 9, 8))
            for _ in range(30)
        ],
        uploaded_by_id=manager_user.id,
    )

    api_client.login("test-employee@example.com", "test-password")
    status, body = api_client.get("/api/dashboards/my-efficiency?month=2026-09")
    assert status == 200, body
    assert body["data"]["manualEfficiencyPercent"] == "100.0"
    assert body["data"]["kaironEfficiencyPercent"] == "100.0"
    assert body["data"]["manualCpd"] == "30.0"
    assert body["data"]["kaironCpd"] == "30.0"
    assert body["data"]["loginDays"] == 1
    assert body["data"]["daily"][0]["manualCharts"] == 30
    assert body["data"]["daily"][0]["kaironCharts"] == 30


def test_bulk_approve_skips_missing_and_non_pending(lead_user):
    owner = _get_or_create_user("reports-bulk-owner1@example.com", "BulkOwner", "One", "TEST-RPT-BLK-1")
    manager_id = lead_user.id
    owner.reports_to_id = lead_user.id

    pending = upsert_own_record(owner.id, _manual_entry(record_date=dt.date(2026, 9, 1)))
    already_approved = upsert_own_record(owner.id, _manual_entry(record_date=dt.date(2026, 9, 2)))
    approve_record(already_approved, manager_id)
    db.session.commit()

    result = bulk_approve_manual_records([pending.id, already_approved.id, 9999999], manager_id)

    assert result["approved"] == [pending.id]
    assert {s["id"] for s in result["skipped"]} == {already_approved.id, 9999999}

    db.session.refresh(pending)
    assert pending.status == "approved"
    assert pending.reviewed_by_id == manager_id


def test_bulk_reject_applies_a_reason_per_record(lead_user):
    owner = _get_or_create_user("reports-bulk-owner2@example.com", "BulkOwner", "Two", "TEST-RPT-BLK-2")
    manager_id = lead_user.id
    owner.reports_to_id = lead_user.id

    first = upsert_own_record(owner.id, _manual_entry(record_date=dt.date(2026, 9, 3)))
    second = upsert_own_record(owner.id, _manual_entry(record_date=dt.date(2026, 9, 4)))

    result = bulk_reject_manual_records(
        [{"id": first.id, "reason": "hours look wrong"}, {"id": second.id, "reason": "wrong date"}],
        manager_id,
    )

    assert set(result["rejected"]) == {first.id, second.id}
    db.session.refresh(first)
    db.session.refresh(second)
    assert first.rejection_reason == "hours look wrong"
    assert second.rejection_reason == "wrong date"


def test_get_coding_dashboard_aggregates_within_window_only():
    user_a = _get_or_create_user("reports-dash-a@example.com", "DashA", "User", "TEST-RPT-DASH-A")
    user_b = _get_or_create_user("reports-dash-b@example.com", "DashB", "User", "TEST-RPT-DASH-B")
    manager_id = _first_manager_id()

    # A batch's reporting date does not control the dashboard window. Only
    # each completed chart's completed_date does.
    import_batch(
        dt.date(2026, 9, 5),
        [
            _kairon_row(user_a, "Active"),
            _kairon_row(user_a, "Active"),
            _kairon_row(user_a, "Completed", completed_date=dt.date(2026, 9, 5)),
            _kairon_row(user_a, "Completed", completed_date=dt.date(2026, 8, 31)),
            _kairon_row(user_b, "On Hold"),
        ],
        uploaded_by_id=manager_id,
    )
    # This August batch still contributes because the chart was completed in
    # September; filtering by batch as_of_date would incorrectly drop it.
    import_batch(
        dt.date(2026, 8, 20),
        [_kairon_row(user_b, "Completed", completed_date=dt.date(2026, 9, 6))],
        uploaded_by_id=manager_id,
    )

    upsert_own_record(user_a.id, _manual_entry(record_date=dt.date(2026, 9, 10)))
    # Out-of-window manual record - must not be counted.
    upsert_own_record(user_a.id, _manual_entry(record_date=dt.date(2026, 8, 15), production_count=999))

    approved_record = upsert_own_record(user_b.id, _manual_entry(record_date=dt.date(2026, 9, 12), production_count=20))
    approve_record(approved_record, manager_id)
    db.session.commit()

    idle_user = _get_or_create_user("reports-dash-idle@example.com", "DashIdle", "User", "TEST-RPT-DASH-IDLE")

    cards = {card["user_id"]: card for card in get_coding_dashboard(dt.date(2026, 9, 1), dt.date(2026, 9, 30))}

    card_a = cards[user_a.id]
    assert card_a["kairon"] == {"active": 0, "on_hold": 0, "completed": 1}
    assert card_a["manual"]["production_count"] == 10
    assert card_a["manual"]["tech_issues_downtime_hours"] == Decimal("1")
    assert card_a["manual"]["pending_count"] == 1
    assert card_a["manual"]["record_count"] == 1

    card_b = cards[user_b.id]
    assert card_b["kairon"] == {"active": 0, "on_hold": 0, "completed": 1}
    assert card_b["manual"]["production_count"] == 20
    assert card_b["manual"]["pending_count"] == 0

    # A user with no activity in the window still gets a zero-value card.
    idle_card = cards[idle_user.id]
    assert idle_card["kairon"] == {"active": 0, "on_hold": 0, "completed": 0}
    assert idle_card["manual"]["production_count"] == 0
    assert idle_card["manual"]["record_count"] == 0


def test_get_coding_dashboard_includes_inactive_user_only_through_last_working_day():
    user = _get_or_create_user(
        "reports-dash-inactive@example.com",
        "Historical",
        "Coder",
        "TEST-RPT-DASH-INACTIVE",
    )
    manager_id = _first_manager_id()
    original_active = user.is_active
    original_last_working_day = user.last_working_day

    try:
        user.is_active = False
        user.last_working_day = dt.date(2026, 9, 15)
        db.session.commit()

        import_batch(
            dt.date(2026, 9, 20),
            [
                _kairon_row(user, "Completed", completed_date=dt.date(2026, 9, 12)),
                _kairon_row(user, "Completed", completed_date=dt.date(2026, 9, 18)),
            ],
            uploaded_by_id=manager_id,
        )
        upsert_own_record(
            user.id,
            _manual_entry(record_date=dt.date(2026, 9, 12), production_count=11),
        )
        upsert_own_record(
            user.id,
            _manual_entry(record_date=dt.date(2026, 9, 18), production_count=99),
        )
        db.session.commit()

        september_cards = get_coding_dashboard(
            dt.date(2026, 9, 1), dt.date(2026, 9, 30), include_daily=True
        )
        card = next(item for item in september_cards if item["user_id"] == user.id)
        assert card["is_active"] is False
        assert card["last_working_day"] == dt.date(2026, 9, 15)
        assert card["kairon"]["completed"] == 1
        assert card["manual"]["production_count"] == 11
        assert card["efficiency"]["manual_charts"] == 11
        assert card["efficiency"]["kairon_charts"] == 1
        assert {row["date"] for row in card["efficiency"]["daily"]} == {
            dt.date(2026, 9, 12)
        }

        october_cards = get_coding_dashboard(dt.date(2026, 10, 1), dt.date(2026, 10, 31))
        assert user.id not in {item["user_id"] for item in october_cards}
    finally:
        user.is_active = original_active
        user.last_working_day = original_last_working_day
        db.session.commit()


def test_get_coding_dashboard_filters_program_and_lead_team():
    lead = _get_or_create_user("reports-filter-lead@example.com", "Filter", "Lead", "TEST-RPT-FILTER-LEAD", "lead")
    team_user = _get_or_create_user("reports-filter-team@example.com", "Filter", "Team", "TEST-RPT-FILTER-TEAM")
    outside_user = _get_or_create_user("reports-filter-outside@example.com", "Filter", "Outside", "TEST-RPT-FILTER-OUT")
    manager_id = _first_manager_id()
    team_user.reports_to_id = lead.id
    db.session.commit()

    import_batch(
        dt.date(2026, 9, 5),
        [
            _kairon_row(team_user, "Active", program="PVP"),
            _kairon_row(
                team_user,
                "Completed",
                program="Foundation",
                completed_date=dt.date(2026, 9, 5),
            ),
            _kairon_row(outside_user, "Active", program="Foundation"),
        ],
        uploaded_by_id=manager_id,
    )
    upsert_own_record(
        team_user.id,
        _manual_entry(
            record_date=dt.date(2026, 9, 10),
            pvp_count=5,
            foundation_count=7,
        ),
    )

    cards = {
        card["user_id"]: card
        for card in get_coding_dashboard(
            dt.date(2026, 9, 1),
            dt.date(2026, 9, 30),
            program="FOUNDATION",
            lead_id=lead.id,
        )
    }

    assert set(cards) == {lead.id, team_user.id}
    assert cards[team_user.id]["kairon"] == {"active": 0, "on_hold": 0, "completed": 1}
    assert cards[team_user.id]["manual"]["production_count"] == 7
    assert cards[team_user.id]["manual"]["pvp_count"] == 0
    assert cards[team_user.id]["manual"]["foundation_count"] == 7

    team_user.reports_to_id = None
    db.session.commit()


def test_get_coding_dashboard_excludes_active_non_coders():
    coding_lead = _get_or_create_user(
        "reports-coding-lead@example.com", "Coding", "Lead", "TEST-RPT-CODING-LEAD", "lead"
    )
    non_coding_employee = _get_or_create_user(
        "reports-noncoding@example.com", "Noncoding", "Employee", "TEST-RPT-NONCODING", project_name=None
    )
    coding_manager = _get_or_create_user(
        "reports-coding-manager@example.com", "Coding", "Manager", "TEST-RPT-CODING-MANAGER", "manager"
    )

    user_ids = {
        card["user_id"]
        for card in get_coding_dashboard(dt.date(2026, 9, 1), dt.date(2026, 9, 30))
    }

    assert coding_lead.id in user_ids
    assert non_coding_employee.id not in user_ids
    assert coding_manager.id not in user_ids


def test_get_coding_dashboard_filters_by_cohort():
    included = _get_or_create_user(
        "reports-cohort-in@example.com", "Cohort", "Included", "TEST-RPT-COHORT-IN"
    )
    excluded = _get_or_create_user(
        "reports-cohort-out@example.com", "Cohort", "Excluded", "TEST-RPT-COHORT-OUT"
    )
    manager_id = _first_manager_id()
    cohort = Cohort(sequence_no=901, label="Dashboard cohort", window_start=dt.date(2026, 9, 1))
    db.session.add(cohort)
    db.session.flush()
    db.session.add(
        CohortMembership(
            cohort_id=cohort.id,
            user_id=included.id,
            joined_on=cohort.window_start,
            assigned_by_id=manager_id,
        )
    )
    db.session.commit()

    cards = get_coding_dashboard(
        dt.date(2026, 9, 1), dt.date(2026, 9, 30), cohort_id=cohort.id
    )
    user_ids = {card["user_id"] for card in cards}

    assert included.id in user_ids
    assert excluded.id not in user_ids


# --------------------------------------------------------------------------
# API-level: self-scoping, review-queue self-exclusion, bulk endpoints, and
# the dashboard's feature gate - through the real encrypted request/
# response cycle (see conftest.ApiClient).
# --------------------------------------------------------------------------


def test_reports_kairon_and_manual_are_scoped_to_self(api_client, employee_user, manager_user):
    other_user = _get_or_create_user("reports-other@example.com", "Other", "User", "TEST-RPT-OTHER")

    import_batch(
        dt.date(2026, 9, 5),
        [_kairon_row(employee_user, "Active"), _kairon_row(other_user, "Active")],
        uploaded_by_id=manager_user.id,
    )
    upsert_own_record(employee_user.id, _manual_entry())
    upsert_own_record(other_user.id, _manual_entry())

    status, _ = api_client.login("test-employee@example.com", "test-password")
    assert status == 200

    status, body = api_client.get("/api/reports/kairon")
    assert status == 200, body
    assert body["data"]["total"] == 1
    assert body["data"]["items"][0]["userId"] == employee_user.id

    status, body = api_client.get("/api/reports/manual")
    assert status == 200, body
    assert body["data"]["total"] == 1
    assert body["data"]["items"][0]["userId"] == employee_user.id


def test_completed_kairon_counts_are_self_scoped_for_employee(api_client, employee_user, manager_user):
    other_user = _get_or_create_user("reports-count-other@example.com", "Count", "Other", "TEST-RPT-COUNT")
    unassigned_team_user = _get_or_create_user(
        "reports-count-unassigned@example.com", "Count", "Unassigned", "TEST-RPT-COUNT-UNASSIGNED"
    )
    lead_user = _get_or_create_user(
        "reports-count-lead@example.com", "Count", "Lead", "TEST-RPT-COUNT-LEAD", "lead"
    )
    team_project = Project.query.filter_by(name="TEST-REPORTS-TEAM").first() or Project(name="TEST-REPORTS-TEAM")
    outside_project = Project.query.filter_by(name="TEST-REPORTS-OUTSIDE").first() or Project(name="TEST-REPORTS-OUTSIDE")
    db.session.add_all([team_project, outside_project])
    db.session.flush()
    manager_user.project_id = team_project.id
    lead_user.project_id = team_project.id
    employee_user.project_id = team_project.id
    unassigned_team_user.project_id = team_project.id
    other_user.project_id = outside_project.id
    lead_user.reports_to_id = manager_user.id
    employee_user.reports_to_id = lead_user.id
    unassigned_team_user.reports_to_id = None
    other_user.reports_to_id = None
    db.session.commit()
    batch = import_batch(
        dt.date(2026, 9, 5),
        [
            _kairon_row(employee_user, "Completed", completed_date=dt.date(2026, 9, 3)),
            _kairon_row(employee_user, "Completed", completed_date=dt.date(2026, 9, 3)),
            _kairon_row(lead_user, "Completed", completed_date=dt.date(2026, 9, 3)),
            _kairon_row(unassigned_team_user, "Completed", completed_date=dt.date(2026, 9, 3)),
            _kairon_row(other_user, "Completed", completed_date=dt.date(2026, 9, 3)),
        ],
        uploaded_by_id=manager_user.id,
    )

    api_client.login("test-employee@example.com", "test-password")
    status, body = api_client.get("/api/reports/kairon/completed-counts")
    assert status == 200, body
    assert body["data"]["items"] == [{"date": "2026-09-03", "count": 2}]
    assert body["data"]["total"] == 1

    status, body = api_client.get(
        "/api/reports/kairon/completed-users?completedDate=2026-09-03"
    )
    assert status == 200, body
    assert body["data"]["items"] == [
        {
            "userId": employee_user.id,
            "firstName": employee_user.first_name,
            "lastName": employee_user.last_name,
            "count": 2,
        }
    ]

    status, body = api_client.get(
        f"/api/reports/kairon/completed-records?completedDate=2026-09-03&userId={employee_user.id}&page=1&pageSize=1"
    )
    assert status == 200, body
    assert body["data"]["total"] == 2
    assert len(body["data"]["items"]) == 1
    own_record_id = body["data"]["items"][0]["id"]

    status, body = api_client.get(
        f"/api/reports/kairon/completed-records?completedDate=2026-09-03&userId={other_user.id}"
    )
    assert status == 200, body
    assert body["data"]["total"] == 0

    status, body = api_client.delete(f"/api/reports/kairon/records/{own_record_id}")
    assert status == 403, body

    api_client.login("test-manager@example.com", "test-password")
    status, body = api_client.get("/api/reports/kairon/completed-counts")
    assert status == 200, body
    assert body["data"]["items"] == [{"date": "2026-09-03", "count": 4}]
    assert body["data"]["total"] == 1

    status, body = api_client.get(
        "/api/reports/kairon/completed-users?completedDate=2026-09-03"
    )
    assert status == 200, body
    assert body["data"]["total"] == 3
    assert {item["userId"]: item["count"] for item in body["data"]["items"]} == {
        employee_user.id: 2,
        lead_user.id: 1,
        unassigned_team_user.id: 1,
    }

    status, body = api_client.get(
        "/api/reports/kairon/completed-users?completedDate=2026-09-03&analyst=Test%20Employee"
    )
    assert status == 200, body
    assert body["data"]["items"] == [
        {
            "userId": employee_user.id,
            "firstName": employee_user.first_name,
            "lastName": employee_user.last_name,
            "count": 2,
        }
    ]

    status, body = api_client.get(
        f"/api/reports/kairon/completed-records?completedDate=2026-09-03&userId={employee_user.id}"
    )
    assert status == 200, body
    assert body["data"]["total"] == 2
    assert all(item["userId"] == employee_user.id for item in body["data"]["items"])

    record_id = body["data"]["items"][0]["id"]
    status, body = api_client.delete(f"/api/reports/kairon/records/{record_id}")
    assert status == 200, body
    assert body["data"] == {"id": record_id}
    assert db.session.get(KaironChartRecord, record_id) is None
    assert KaironChartAnalystAction.query.filter_by(chart_record_id=record_id).count() == 0
    assert KaironChartRecord.query.filter_by(batch_id=batch.id).count() == 4

    status, body = api_client.get("/api/reports/kairon/completed-counts")
    assert status == 200, body
    assert body["data"]["items"] == [{"date": "2026-09-03", "count": 3}]

    status, body = api_client.get(
        "/api/reports/kairon/completed-users?completedDate=2026-09-03&analyst=Test%20Employee"
    )
    assert status == 200, body
    assert body["data"]["items"][0]["count"] == 1

    employee_user.reports_to_id = None
    lead_user.reports_to_id = None
    for user in (manager_user, lead_user, employee_user, unassigned_team_user, other_user):
        user.project_id = None
    db.session.commit()


def test_reports_manual_reviews_excludes_reviewers_own_records(api_client, employee_user, manager_user):
    lead_user = _get_or_create_user(
        "reports-review-lead@example.com", "Review", "Lead", "TEST-RPT-REVIEW-LEAD", "lead"
    )
    outside_user = _get_or_create_user(
        "reports-review-outside@example.com", "Review", "Outside", "TEST-RPT-REVIEW-OUTSIDE"
    )
    team_project = Project.query.filter_by(name="TEST-REPORTS-REVIEW-TEAM").first() or Project(
        name="TEST-REPORTS-REVIEW-TEAM"
    )
    outside_project = Project.query.filter_by(name="TEST-REPORTS-REVIEW-OUTSIDE").first() or Project(
        name="TEST-REPORTS-REVIEW-OUTSIDE"
    )
    db.session.add_all([team_project, outside_project])
    db.session.flush()
    manager_user.project_id = team_project.id
    lead_user.project_id = team_project.id
    employee_user.project_id = team_project.id
    outside_user.project_id = outside_project.id
    lead_user.reports_to_id = manager_user.id
    employee_user.reports_to_id = lead_user.id
    outside_user.reports_to_id = None
    db.session.commit()

    api_client.login("test-manager@example.com", "test-password")
    status, body = api_client.post("/api/manual-daily-records", _manual_entry_body(date="2026-09-11"))
    assert status == 200, body

    status, body = api_client.post("/api/manual-daily-records", _manual_entry_body(date="2026-09-12"))
    assert status == 200, body

    api_client.login("test-employee@example.com", "test-password")
    status, body = api_client.post("/api/manual-daily-records", _manual_entry_body(date="2026-09-13"))
    assert status == 200, body

    api_client.login("reports-review-outside@example.com", "test-password")
    status, body = api_client.post("/api/manual-daily-records", _manual_entry_body(date="2026-09-13"))
    assert status == 200, body

    api_client.login("test-manager@example.com", "test-password")
    status, body = api_client.get("/api/reports/manual/reviews")
    assert status == 200, body
    returned_user_ids = {row["userId"] for row in body["data"]["items"]}
    assert returned_user_ids == {employee_user.id}

    status, body = api_client.get(f"/api/reports/manual/reviews?leadId={lead_user.id}")
    assert status == 200, body
    assert {row["userId"] for row in body["data"]["items"]} == {employee_user.id}

    status, body = api_client.get("/api/reports/manual/reviews?leadId=9999999")
    assert status == 400, body

    employee_user.reports_to_id = None
    lead_user.reports_to_id = None
    for user in (manager_user, lead_user, employee_user, outside_user):
        user.project_id = None
    db.session.commit()


def test_reports_manual_uses_server_side_pagination(api_client, employee_user):
    api_client.login("test-employee@example.com", "test-password")
    for day in range(1, 4):
        status, body = api_client.post(
            "/api/manual-daily-records", _manual_entry_body(date=f"2026-09-0{day}")
        )
        assert status == 200, body

    status, body = api_client.get("/api/reports/manual?page=2&pageSize=2")
    assert status == 200, body
    assert body["data"]["page"] == 2
    assert body["data"]["pageSize"] == 2
    assert body["data"]["total"] == 3
    assert body["data"]["totalPages"] == 2
    assert len(body["data"]["items"]) == 1


def test_reports_reviews_and_bulk_endpoints_reject_employee_role(api_client, employee_user):
    api_client.login("test-employee@example.com", "test-password")

    status, body = api_client.get("/api/reports/manual/reviews")
    assert status == 403, body

    status, body = api_client.post("/api/reports/manual/reviews/bulk-approve", {"ids": [1]})
    assert status == 403, body

    status, body = api_client.post("/api/reports/manual/reviews/bulk-reject", {"items": [{"id": 1, "reason": "x"}]})
    assert status == 403, body


def test_bulk_approve_endpoint_approves_and_reports_skips(api_client, employee_user, lead_user):
    api_client.login("test-employee@example.com", "test-password")
    status, body = api_client.post("/api/manual-daily-records", _manual_entry_body(date="2026-09-14"))
    record_id = body["data"]["id"]

    api_client.login(lead_user.email, "test-password")
    status, body = api_client.post("/api/reports/manual/reviews/bulk-approve", {"ids": [record_id, 9999999]})
    assert status == 200, body
    assert body["data"]["approved"] == [record_id]
    assert body["data"]["skipped"][0]["id"] == 9999999


def test_bulk_reject_endpoint_applies_reason_per_record(api_client, employee_user, lead_user):
    api_client.login("test-employee@example.com", "test-password")
    status, body = api_client.post("/api/manual-daily-records", _manual_entry_body(date="2026-09-15"))
    record_id = body["data"]["id"]

    api_client.login(lead_user.email, "test-password")
    status, body = api_client.post(
        "/api/reports/manual/reviews/bulk-reject", {"items": [{"id": record_id, "reason": "hours look wrong"}]}
    )
    assert status == 200, body
    assert body["data"]["rejected"] == [record_id]


def test_team_dashboard_uses_dashboard_feature_and_privileged_role(api_client, employee_user, manager_user):
    api_client.login("test-employee@example.com", "test-password")
    status, body = api_client.get("/api/dashboards/coding")
    assert status == 403, body

    api_client.login("test-manager@example.com", "test-password")
    status, body = api_client.get("/api/dashboards/coding")
    assert status == 200, body


def test_coding_dashboard_returns_expected_card_for_a_user(api_client, employee_user, manager_user):
    import_batch(
        dt.date(2026, 9, 5),
        [
            _kairon_row(employee_user, "Active"),
            _kairon_row(employee_user, "Completed", completed_date=dt.date(2026, 9, 5)),
        ],
        uploaded_by_id=manager_user.id,
    )

    api_client.login("test-employee@example.com", "test-password")
    status, body = api_client.post("/api/manual-daily-records", _manual_entry_body(date="2026-09-16"))
    assert status == 200, body

    api_client.login("test-manager@example.com", "test-password")
    status, body = api_client.get("/api/dashboards/coding?from=2026-09-01&to=2026-09-30&includeDaily=true")
    assert status == 200, body

    card = next(row for row in body["data"] if row["userId"] == employee_user.id)
    assert card["isActive"] is True
    assert card["lastWorkingDay"] is None
    assert card["leadId"] == employee_user.reports_to_id
    assert card["kairon"] == {"active": 0, "onHold": 0, "completed": 1}
    assert card["manual"]["productionCount"] == 10
    assert {row["date"] for row in card["efficiency"]["daily"]} == {"2026-09-05", "2026-09-16"}


@pytest.mark.parametrize("endpoint", ["manual", "kairon/completed-counts"])
def test_reports_shared_date_range_filters_before_pagination(api_client, employee_user, manager_user, endpoint):
    other = _get_or_create_user("reports-range-other@example.com", "Range", "Other", "TEST-RPT-RANGE")
    dates = [dt.date(2026, 9, 9), dt.date(2026, 9, 10), dt.date(2026, 10, 5), dt.date(2026, 10, 6)]
    if endpoint == "manual":
        for user in (employee_user, other):
            for date in dates:
                upsert_own_record(user.id, _manual_entry(record_date=date))
    else:
        import_batch(dt.date(2026, 10, 7), [
            _kairon_row(user, "Completed", completed_date=date)
            for user in (employee_user, other) for date in dates
        ], uploaded_by_id=manager_user.id)
    api_client.login("test-employee@example.com", "test-password")
    base = f"/api/reports/{endpoint}"
    status, body = api_client.get(base + "?fromDate=2026-09-10&toDate=2026-10-05&pageSize=1&page=2")
    assert status == 200, body
    assert body["data"]["total"] == 2
    assert body["data"]["totalPages"] == 2
    assert [item["date"] for item in body["data"]["items"]] == ["2026-09-10"]
    if endpoint == "manual":
        assert body["data"]["items"][0]["userId"] == employee_user.id
    else:
        assert body["data"]["items"][0]["count"] == 1
    for query, expected in [
        ("fromDate=2026-10-05&toDate=2026-10-05", ["2026-10-05"]),
        ("fromDate=2026-10-06", ["2026-10-06"]),
        ("toDate=2026-09-09", ["2026-09-09"]),
        ("fromDate=2026-11-01&toDate=2026-11-30", []),
    ]:
        status, body = api_client.get(base + "?" + query)
        assert status == 200, body
        assert [item["date"] for item in body["data"]["items"]] == expected
    for query in ["fromDate=2026-10-05&toDate=2026-09-10", "fromDate=invalid", "toDate=2026-02-30"]:
        status, _ = api_client.get(base + "?" + query)
        assert status in (400, 422)
