import datetime as dt
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import event

from app.cohorts.models import Cohort, CohortMembership, UserStagePeriod
from app.encryption.passwords import hash_password
from app.extensions import db
from app.kairon.models import KaironChartRecord, KaironUploadBatch
from app.manual_daily_records import productivity
from app.manual_daily_records.services import upsert_own_record
from app.reports.manager_dashboard import get_manager_dashboard
from app.roles.models import Role, RoleType
from app.users.models import Project, User

DAY = dt.date(2026, 9, 10)


@pytest.fixture
def manager_team(app):
    token = uuid4().hex[:10]
    project = Project(name=f"manager-test-{token}")
    db.session.add(project)
    db.session.flush()
    roles = {code: Role.query.join(RoleType).filter(RoleType.code == code).first() for code in ("manager", "lead", "employee")}
    members = []
    # Manager, QA A, QA B, two A coders, one B coder, outside manager/lead/coder, unassigned.
    specs = [("Manager", "manager", None), ("QA A", "lead", 0), ("QA B", "lead", 0),
             ("Coder A", "employee", 1), ("Coder B", "employee", 1), ("Coder C", "employee", 2),
             ("Outside manager", "manager", None), ("Outside QA", "lead", 6), ("Outside coder", "employee", 7),
             ("Unassigned", "employee", None)]
    for index, (name, role, parent) in enumerate(specs):
        user = User(email=f"manager-dash-{token}-{index}@example.com", first_name=name, last_name="Test",
                    emp_id=f"MD-{token}-{index}", role_id=roles[role].id, project_id=project.id,
                    reports_to_id=members[parent].id if parent is not None else None,
                    password_hash=hash_password("test-password"), first_login=False, is_active=True)
        db.session.add(user)
        db.session.flush()
        members.append(user)
        db.session.add(UserStagePeriod(user_id=user.id, stage_code="M1" if index == 4 else "Steady State",
                                       start_date=dt.date(2026, 9, 1), source="manual_override"))
    cohort = Cohort(sequence_no=1, label="Selected cohort", window_start=DAY)
    db.session.add(cohort)
    db.session.flush()
    db.session.commit()
    for index, user in enumerate(members):
        count = (888, 100, 20, 30, 7, 15, 777, 666, 555, 444)[index]
        upsert_own_record(user.id, dict(record_date=DAY, production_count=count, pvp_count=count - 1, foundation_count=1,
            tech_issues_downtime_hours=4 if index == 4 else 0, no_inventory_idle_time_hours=0,
            leave_hours=0, meeting_engagement_hours=0))
    batch = KaironUploadBatch(uploaded_by_id=members[0].id, as_of_date=DAY)
    db.session.add(batch)
    db.session.flush()
    for user in members:
        for program in ("PVP", "FOUNDATION"):
            db.session.add(KaironChartRecord(batch_id=batch.id, user_id=user.id, program=program, level="1LR",
                status="Completed", coding_analyst_raw=user.first_name, created_date=DAY, completed_date=DAY))
    for index in (1, 3):
        db.session.add(CohortMembership(cohort_id=cohort.id, user_id=members[index].id, joined_on=DAY, assigned_by_id=members[0].id))
    db.session.commit()
    return members, cohort


def read_dashboard(api_client, manager, query="date=2026-09-10"):
    api_client.login(manager.email, "test-password")
    status, response = api_client.get(f"/api/dashboards/manager?{query}")
    assert status == 200, response
    return response["data"]


def test_separate_scoped_totals_and_weighted_team_rollups(manager_team, api_client, monkeypatch):
    members, _ = manager_team
    monkeypatch.setattr(productivity, "adjusted_cpd", lambda *a, **kw: pytest.fail("Read must use saved CPD"))
    data = read_dashboard(api_client, members[0])
    assert {lead["userId"] for lead in data["leadOptions"]} == {members[1].id, members[2].id}
    assert {coder["userId"] for coder in data["coderOptions"]} == {member.id for member in members[3:6]}
    assert data["qa"]["efficiency"]["manualCharts"] == 120
    assert data["qa"]["efficiency"]["kaironCharts"] == 4
    assert data["coders"]["efficiency"]["manualCharts"] == 52
    assert data["coders"]["efficiency"]["kaironCharts"] == 6
    assert data["qa"]["goal"]["targetCharts"] == 60
    assert data["coders"]["goal"]["adjustedTargetCharts"] == "63.50"
    first = next(team for team in data["teams"] if team["lead"]["userId"] == members[1].id)
    assert first["qa"]["efficiency"]["manualCharts"] == 100
    assert first["coders"]["efficiency"]["manualCharts"] == 37
    assert first["coders"]["efficiency"]["manualCpd"] == "24.7"
    assert first["coders"]["efficiency"]["manualEfficiencyPercent"] == "110.4"
    assert first["coders"]["efficiency"]["daily"][0]["adjustedCpd"] == "33.50"
    assert all("daily" not in member["efficiency"] for member in data["members"])
    assert len(data["members"]) == 5
    assert data["overall"]["manualCharts"] == 172
    assert data["overall"]["kaironCharts"] == 10
    assert data["overall"]["targetMinutes"] == 2160
    assert data["overall"]["adjustedTarget"] == "123.50"
    assert data["overall"]["manualCpd"] == "38.2"
    assert data["overall"]["manualEfficiencyPercent"] == "120.0"
    assert data["overall"]["kaironEfficiencyPercent"] == "8.1"
    assert "daily" not in data["overall"]


def test_coder_selection_limits_team_and_keeps_qa_separate(manager_team, api_client):
    members, _ = manager_team
    data = read_dashboard(api_client, members[0], f"date=2026-09-10&coderId={members[4].id}")
    assert data["qa"]["efficiency"]["manualCharts"] == 100
    assert data["coders"]["efficiency"]["manualCharts"] == 7
    assert data["coders"]["goal"]["userCount"] == 1
    assert len(data["teams"]) == 1
    assert len(data["coderOptions"]) == 3
    assert len(data["leadOptions"]) == 2
    assert data["overall"]["manualCharts"] == 107
    assert data["overall"]["manualCpd"] == "71.3"


@pytest.mark.parametrize("query", ["month=2026-09", "year=2026", "from=2026-04-01&to=2026-10-04", "from=2026-09-10&to=2026-09-11"])
def test_all_period_modes(manager_team, api_client, query):
    members, _ = manager_team
    data = read_dashboard(api_client, members[0], query)
    assert data["qa"]["efficiency"]["manualCharts"] == 120
    assert data["coders"]["efficiency"]["manualCharts"] == 52
    assert data["qa"]["goal"]["to"] == ("2026-09-30" if query.startswith("month") else data["to"])


def test_lead_cohort_and_program_filters(manager_team, api_client):
    members, cohort = manager_team
    data = read_dashboard(api_client, members[0], f"date=2026-09-10&leadId={members[1].id}&cohortId={cohort.id}&program=FOUNDATION")
    assert len(data["teams"]) == 1
    for section in (data["qa"], data["coders"]):
        assert section["goal"]["userCount"] == 1
        assert section["goal"]["manualCharts"] == section["efficiency"]["manualCharts"] == 1
        assert section["goal"]["completedCharts"] == section["efficiency"]["kaironCharts"] == 1
        assert section["goal"]["targetCharts"] == 30
        assert section["efficiency"]["targetMinutes"] == 480


@pytest.mark.parametrize("case", ["outside_lead", "outside_coder", "qa_as_coder", "other_team_coder", "cohort_mismatch", "invalid_range", "invalid_month", "negative_id"])
def test_invalid_or_unauthorized_filters(manager_team, api_client, case):
    members, cohort = manager_team
    queries = {
        "outside_lead": (f"leadId={members[7].id}", 404),
        "outside_coder": (f"coderId={members[8].id}", 404),
        "qa_as_coder": (f"coderId={members[1].id}", 404),
        "other_team_coder": (f"leadId={members[1].id}&coderId={members[5].id}", 404),
        "cohort_mismatch": (f"cohortId={cohort.id}&coderId={members[4].id}", 404),
        "invalid_range": ("from=2026-09-30&to=2026-09-01", 400),
        "invalid_month": ("month=2026-13", 400),
        "negative_id": ("coderId=-1", 422),
    }
    query, expected = queries[case]
    api_client.login(members[0].email, "test-password")
    status, _ = api_client.get(f"/api/dashboards/manager?{query}")
    assert status == expected


@pytest.mark.parametrize("index", [1, 3])
def test_other_roles_cannot_access_manager_dashboard(manager_team, api_client, index):
    members, _ = manager_team
    api_client.login(members[index].email, "test-password")
    status, _ = api_client.get("/api/dashboards/manager?date=2026-09-10")
    assert status == 403


def test_departed_lead_team_and_coder_cutoff(manager_team):
    members, _ = manager_team
    for index in (1, 4):
        members[index].is_active = False
        members[index].last_working_day = DAY
    db.session.commit()
    next_day = DAY + dt.timedelta(days=1)
    data = get_manager_dashboard(members[0], next_day, next_day)
    first = next(team for team in data["teams"] if team["lead"]["user_id"] == members[1].id)
    assert first["qa"]["goal"]["user_count"] == 0
    assert first["coders"]["goal"]["user_count"] == 1
    assert members[4].id not in {coder["user_id"] for coder in data["coder_options"]}


def test_unassigned_coders_only_for_single_manager_project(manager_team):
    members, _ = manager_team
    members[6].is_active = False
    db.session.commit()
    data = get_manager_dashboard(members[0], DAY, DAY, coder_id=members[9].id)
    assert data["qa"]["goal"]["user_count"] == 0
    assert data["coders"]["efficiency"]["manual_charts"] == 444
    assert data["teams"][0]["key"] == "unassigned"
    assert data["teams"][0]["lead"] is None


def test_empty_manager_and_no_coder_team(manager_team):
    members, _ = manager_team
    for member in members[3:6]:
        member.reports_to_id = members[7].id
    db.session.commit()
    data = get_manager_dashboard(members[0], DAY, DAY)
    assert data["qa"]["efficiency"]["manual_charts"] == 120
    assert data["coders"]["goal"]["user_count"] == 0
    for member in members[1:3]:
        member.reports_to_id = members[6].id
    db.session.commit()
    data = get_manager_dashboard(members[0], DAY, DAY)
    assert data["teams"] == []
    assert data["coders"]["efficiency"]["daily"] == []
    assert data["qa"]["efficiency"]["manual_cpd"] is None


def test_query_count_does_not_grow_with_team_count(manager_team):
    members, _ = manager_team
    manager = members[0]
    get_manager_dashboard(manager, DAY, DAY)  # Warm relationship metadata before measuring.
    calls = []
    def count_queries(*args):
        calls.append(1)
    event.listen(db.engine, "before_cursor_execute", count_queries)
    try:
        get_manager_dashboard(manager, DAY, DAY, lead_id=members[1].id)
        single_count = len(calls)
        calls.clear()
        data = get_manager_dashboard(manager, DAY, DAY)
        assert len(calls) <= single_count
        assert data["coders"]["goal"]["adjusted_target_charts"] == Decimal("63.50")
    finally:
        event.remove(db.engine, "before_cursor_execute", count_queries)
