import datetime as dt
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import event

from app.cohorts.models import UserStagePeriod
from app.encryption.passwords import hash_password
from app.extensions import db
from app.kairon.models import KaironChartRecord, KaironUploadBatch
from app.manual_daily_records import productivity
from app.manual_daily_records.services import upsert_own_record
from app.reports.lead_dashboard import get_lead_dashboard
from app.roles.models import Role, RoleType
from app.users.models import User


DAY = dt.date(2026, 9, 10)


@pytest.fixture
def team(manager_user):
    token = uuid4().hex[:10]
    password = hash_password("test-password")
    roles = {code: Role.query.join(RoleType).filter(RoleType.code == code).first() for code in ("lead", "employee")}
    members = []
    for index, (name, role) in enumerate((("QA", "lead"), ("Coder A", "employee"), ("Coder B", "employee"), ("Outside", "employee"))):
        user = User(email=f"lead-dashboard-{token}-{index}@example.com", first_name=name, last_name="Test",
                    emp_id=f"LD-{token}-{index}", role_id=roles[role].id, project_id=manager_user.project_id,
                    reports_to_id=members[0].id if index in (1, 2) else manager_user.id if index == 0 else None,
                    password_hash=password, first_login=False, is_active=True)
        db.session.add(user)
        db.session.flush()
        members.append(user)
        db.session.add(UserStagePeriod(user_id=user.id, stage_code="M1" if index == 2 else "Steady State",
                                       start_date=dt.date(2026, 9, 1), source="manual_override"))
    db.session.commit()
    for index, user in enumerate(members):
        upsert_own_record(user.id, dict(record_date=DAY, production_count=(100, 30, 7, 999)[index],
            tech_issues_downtime_hours=4 if index == 2 else 0, no_inventory_idle_time_hours=0,
            leave_hours=0, meeting_engagement_hours=0))
    batch = KaironUploadBatch(uploaded_by_id=manager_user.id, as_of_date=DAY)
    db.session.add(batch)
    db.session.flush()
    for index, user in enumerate(members):
        for _ in range(index + 1):
            db.session.add(KaironChartRecord(batch_id=batch.id, user_id=user.id, program="PVP", level="1LR",
                status="Completed", coding_analyst_raw=user.first_name, created_date=DAY, completed_date=DAY))
    db.session.commit()
    return members


def test_all_coders_are_separate_from_qa_and_weighted_from_raw_totals(team, api_client, monkeypatch):
    qa, a, b, outsider = team
    monkeypatch.setattr(productivity, "adjusted_cpd", lambda *args, **kwargs: pytest.fail("Read must use saved CPD"))
    api_client.login(qa.email, "test-password")
    status, response = api_client.get("/api/dashboards/lead?date=2026-09-10")
    assert status == 200, response
    data = response["data"]
    assert {row["userId"] for row in data["coderOptions"]} == {a.id, b.id}
    assert data["selectedCoderId"] is None
    assert data["qa"]["goal"]["userCount"] == 1
    assert data["qa"]["efficiency"]["manualCharts"] == 100
    assert data["qa"]["efficiency"]["kaironCharts"] == 1
    assert data["qa"]["efficiency"]["adjustedCpd"] == "30.00"
    coders = data["coders"]
    assert coders["goal"]["userCount"] == 2
    assert coders["goal"]["targetCharts"] == 37
    assert coders["goal"]["adjustedTargetCharts"] == "33.50"
    assert coders["efficiency"]["manualCharts"] == 37
    assert coders["efficiency"]["kaironCharts"] == 5
    assert coders["efficiency"]["adjustedCpd"] == "33.50"
    assert coders["efficiency"]["manualCpd"] == "24.7"
    assert coders["efficiency"]["manualEfficiencyPercent"] == "110.4"
    day = coders["efficiency"]["daily"][0]
    assert day["stage"] == "Mixed stages"
    assert day["adjustedCpd"] == "33.50"
    assert day["manualEfficiencyPercent"] == "110.4"


def test_coder_selection_changes_only_coders_and_keeps_all_options(team, api_client):
    qa, a, b, _ = team
    api_client.login(qa.email, "test-password")
    _, all_data = api_client.get("/api/dashboards/lead?month=2026-09")
    status, response = api_client.get(f"/api/dashboards/lead?month=2026-09&coderId={b.id}")
    assert status == 200, response
    data = response["data"]
    assert data["qa"] == all_data["data"]["qa"]
    assert len(data["coderOptions"]) == 2
    assert data["coders"]["goal"]["userCount"] == 1
    assert data["coders"]["efficiency"]["manualCharts"] == 7
    assert data["coders"]["efficiency"]["adjustedCpd"] == "3.50"
    assert data["coders"]["efficiency"]["manualCpd"] == "14.0"
    assert data["coders"]["efficiency"]["manualEfficiencyPercent"] == "120.0"


@pytest.mark.parametrize("member_index", [0, 3])
def test_cannot_select_qa_or_someone_outside_the_lead(team, api_client, member_index):
    api_client.login(team[0].email, "test-password")
    status, _ = api_client.get(f"/api/dashboards/lead?date=2026-09-10&coderId={team[member_index].id}")
    assert status == 404


def test_employees_cannot_access_lead_dashboard(team, api_client):
    api_client.login(team[1].email, "test-password")
    status, _ = api_client.get("/api/dashboards/lead?month=2026-09")
    assert status == 403


@pytest.mark.parametrize("query", ["date=2026-09-10", "month=2026-09", "year=2026", "from=2026-09-01&to=2026-09-30"])
def test_all_manager_period_modes_scope_both_sections(team, api_client, query):
    api_client.login(team[0].email, "test-password")
    status, response = api_client.get(f"/api/dashboards/lead?{query}")
    assert status == 200, response
    assert response["data"]["qa"]["efficiency"]["manualCharts"] == 100
    assert response["data"]["coders"]["efficiency"]["manualCharts"] == 37


def test_invalid_period_is_rejected(team, api_client):
    api_client.login(team[0].email, "test-password")
    for query in ("from=2026-09-30&to=2026-09-01", "month=2026-13"):
        status, _ = api_client.get(f"/api/dashboards/lead?{query}")
        assert status == 400


def test_no_coders_gives_empty_rollup_and_preserves_qa(team):
    qa, a, b, _ = team
    a.reports_to_id = b.reports_to_id = None
    db.session.commit()
    data = get_lead_dashboard(qa, DAY, DAY)
    assert data["coder_options"] == []
    assert data["coders"]["goal"]["target_charts"] == 0
    assert data["coders"]["efficiency"]["daily"] == []
    assert data["coders"]["efficiency"]["manual_cpd"] is None
    assert data["qa"]["efficiency"]["manual_charts"] == 100


def test_historical_coder_ends_at_last_working_day(team):
    qa, _, b, _ = team
    b.is_active = False
    b.last_working_day = DAY
    db.session.commit()
    assert b.id in {row["user_id"] for row in get_lead_dashboard(qa, DAY, DAY)["coder_options"]}
    next_day = DAY + dt.timedelta(days=1)
    data = get_lead_dashboard(qa, next_day, next_day)
    assert b.id not in {row["user_id"] for row in data["coder_options"]}
    assert data["coders"]["efficiency"]["manual_charts"] == 0


def test_rollup_batches_queries_instead_of_querying_each_coder(team):
    qa, _, b, _ = team
    calls = []
    def count_queries(*args):
        calls.append(1)
    event.listen(db.engine, "before_cursor_execute", count_queries)
    try:
        get_lead_dashboard(qa, DAY, DAY, b.id)
        single_count = len(calls)
        calls.clear()
        data = get_lead_dashboard(qa, DAY, DAY)
        assert len(calls) <= single_count
        assert data["coders"]["goal"]["adjusted_target_charts"] == Decimal("33.50")
    finally:
        event.remove(db.engine, "before_cursor_execute", count_queries)
