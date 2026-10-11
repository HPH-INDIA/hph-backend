"""Coverage grants are checked server-side for reads, filters, and review writes."""
import datetime as dt
from uuid import uuid4

import pytest

from app.encryption.passwords import hash_password
from app.extensions import db
from app.kairon.models import KaironChartRecord, KaironUploadBatch
from app.lead_coverage.models import LeadCoverage
from app.lead_coverage import services as coverage_services
from app.manual_daily_records.models import ManualDailyRecord
from app.roles.models import Role, RoleType
from app.users.models import User


TODAY = dt.date(2026, 10, 12)
DATA_DAY = dt.date(2026, 9, 10)
RANGE = "fromDate=2026-09-01&toDate=2026-09-30"


@pytest.fixture
def coverage_team(manager_user, monkeypatch):
    monkeypatch.setattr(coverage_services, "business_today", lambda: TODAY)
    token = uuid4().hex[:10]
    password = hash_password("test-password")
    roles = {code: Role.query.join(RoleType).filter(RoleType.code == code).first()
             for code in ("lead", "employee")}
    users = {}
    for name in ("covering", "source", "unrelated"):
        for role in ("lead", "employee"):
            key = name if role == "lead" else f"{name}_coder"
            user = User(
                email=f"coverage-access-{token}-{key}@example.com", first_name=key,
                last_name="Coverage", emp_id=f"CA-{token}-{key}", role_id=roles[role].id,
                project_id=manager_user.project_id,
                reports_to_id=manager_user.id if role == "lead" else users[name].id,
                password_hash=password, first_login=False, is_active=True,
            )
            db.session.add(user)
            db.session.flush()
            users[key] = user
    grant = LeadCoverage(source_lead_id=users["source"].id, covering_lead_id=users["covering"].id,
                         starts_on=TODAY, ends_on=TODAY, status="approved",
                         created_by_id=manager_user.id, decided_by_id=manager_user.id)
    db.session.add(grant)
    batch = KaironUploadBatch(uploaded_by_id=manager_user.id, as_of_date=DATA_DAY)
    db.session.add(batch)
    db.session.flush()
    manuals = {}
    for key, user in users.items():
        record = ManualDailyRecord(user_id=user.id, record_date=DATA_DAY, production_count=5,
                                   pvp_count=5, foundation_count=0, status="pending")
        db.session.add(record)
        manuals[key] = record
        for status in ("Completed", "On Hold"):
            db.session.add(KaironChartRecord(
                batch_id=batch.id, user_id=user.id, program="PVP", level="1LR", status=status,
                coding_analyst_raw=key, created_date=DATA_DAY,
                completed_date=DATA_DAY if status == "Completed" else None,
            ))
    db.session.commit()
    return users, grant, manuals


def login(client, user):
    status, body = client.login(user.email, "test-password")
    assert status == 200, body


@pytest.mark.parametrize("state,offset,active", [
    ("approved", 0, True), ("pending", 0, False), ("rejected", 0, False),
    ("cancelled", 0, False), ("approved", 1, False), ("approved", -1, False),
])
def test_only_current_approved_coverage_grants_report_access(coverage_team, api_client, state, offset, active):
    users, grant, _ = coverage_team
    grant.status = state
    grant.starts_on = grant.ends_on = TODAY + dt.timedelta(days=offset)
    db.session.commit()
    login(api_client, users["covering"])

    status, body = api_client.get(f"/api/dashboards/lead?date={DATA_DAY}&leadId={users['source'].id}")
    assert status == (200 if active else 404), body
    status, body = api_client.get(f"/api/reports/kairon/team-range?{RANGE}&leadId={users['source'].id}")
    assert status == (200 if active else 404), body
    status, body = api_client.get(f"/api/kairon/charts?userId={users['source_coder'].id}")
    assert status == 200, body
    assert bool(body["data"]) == active
    status, body = api_client.get(f"/api/reports/manual/reviews?userId={users['source_coder'].id}")
    assert status == 200, body
    assert bool(body["data"]["items"]) == active


def test_manual_reads_group_original_teams_and_reviews_record_actual_actor(coverage_team, api_client):
    users, _, manuals = coverage_team
    login(api_client, users["covering"])
    status, body = api_client.get(f"/api/reports/manual/team-range?{RANGE}")
    assert status == 200, body
    teams = {team["lead"]["id"]: team for team in body["data"]["teams"]}
    assert set(teams) == {users["covering"].id, users["source"].id}
    assert teams[users["source"].id]["leadRecords"][0]["id"] == manuals["source"].id
    status, body = api_client.get(f"/api/reports/manual/reviews?leadId={users['source'].id}")
    assert status == 200, body
    assert [row["id"] for row in body["data"]["items"]] == [manuals["source_coder"].id]

    status, body = api_client.post(f"/api/manual-daily-records/{manuals['source_coder'].id}/approve")
    assert status == 200, body
    assert db.session.get(ManualDailyRecord, manuals["source_coder"].id).reviewed_by_id == users["covering"].id
    for key in ("source", "covering", "unrelated_coder"):
        status, _ = api_client.post(f"/api/manual-daily-records/{manuals[key].id}/approve")
        assert status == 404
    assert users["source_coder"].reports_to_id == users["source"].id


def test_bulk_review_never_approves_leads_or_unrelated_employees(coverage_team, api_client):
    users, _, manuals = coverage_team
    login(api_client, users["covering"])
    status, body = api_client.post("/api/reports/manual/reviews/bulk-approve", {
        "ids": [manuals[key].id for key in ("source_coder", "source", "unrelated_coder")],
    })
    assert status == 200, body
    assert body["data"]["approved"] == [manuals["source_coder"].id]
    assert {row["id"] for row in body["data"]["skipped"]} == {manuals["source"].id, manuals["unrelated_coder"].id}


def test_selected_dashboard_and_kairon_team_validate_every_drilldown(coverage_team, api_client):
    users, _, _ = coverage_team
    login(api_client, users["covering"])
    source_id = users["source"].id
    status, body = api_client.get(f"/api/dashboards/lead?date={DATA_DAY}&leadId={source_id}")
    assert status == 200, body
    assert body["data"]["selectedLeadId"] == source_id
    assert {row["leadId"] for row in body["data"]["teamOptions"]} == {source_id, users["covering"].id}
    assert [row["userId"] for row in body["data"]["coderOptions"]] == [users["source_coder"].id]
    status, body = api_client.get(f"/api/reports/kairon/team-range?{RANGE}&leadId={source_id}")
    assert status == 200, body
    assert body["data"]["lead"]["id"] == source_id
    assert body["data"]["selectedLeadId"] == source_id
    status, body = api_client.get(f"/api/reports/kairon/team-records?{RANGE}&leadId={source_id}&userId={users['source_coder'].id}")
    assert status == 200, body
    assert len(body["data"]["items"]) == 1
    for key in ("covering_coder", "unrelated_coder"):
        status, _ = api_client.get(f"/api/reports/kairon/team-records?{RANGE}&leadId={source_id}&userId={users[key].id}")
        assert status == 404
        status, _ = api_client.get(f"/api/dashboards/lead?date={DATA_DAY}&leadId={source_id}&coderId={users[key].id}")
        assert status == 404


def test_hold_and_stage_drilldowns_read_covered_team_but_personal_reports_stay_self(coverage_team, api_client):
    users, _, _ = coverage_team
    login(api_client, users["covering"])
    status, body = api_client.get(f"/api/reports/kairon/holds-summary?leadId={users['source'].id}")
    assert status == 200, body
    assert body["data"]["total"] == 2
    status, _ = api_client.get(f"/api/reports/kairon/holds?userId={users['unrelated_coder'].id}")
    assert status == 404
    status, body = api_client.get(f"/api/team/coders?leadId={users['source'].id}")
    assert status == 200, body
    assert body["data"]["total"] == 2
    status, body = api_client.get("/api/reports/kairon")
    assert status == 200, body
    assert {row["userId"] for row in body["data"]["items"]} == {users["covering"].id}
    status, body = api_client.get("/api/reports/manual")
    assert status == 200, body
    assert {row["userId"] for row in body["data"]["items"]} == {users["covering"].id}


def test_expiration_revokes_existing_session_reads_and_writes(coverage_team, api_client, monkeypatch):
    users, _, manuals = coverage_team
    login(api_client, users["covering"])
    status, _ = api_client.get(f"/api/reports/kairon/team-records?{RANGE}&userId={users['source_coder'].id}")
    assert status == 200
    monkeypatch.setattr(coverage_services, "business_today", lambda: TODAY + dt.timedelta(days=1))
    status, _ = api_client.get(f"/api/reports/kairon/team-records?{RANGE}&userId={users['source_coder'].id}")
    assert status == 404
    status, _ = api_client.post(f"/api/manual-daily-records/{manuals['source_coder'].id}/reject", {"reason": "Review"})
    assert status == 404
    status, body = api_client.get(f"/api/kairon/charts?userId={users['source_coder'].id}")
    assert status == 200, body
    assert body["data"] == []
