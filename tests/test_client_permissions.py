from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from werkzeug.exceptions import HTTPException

from app.auth import has_feature
from app.encryption.passwords import hash_password
from app.extensions import db
from app.features.models import Feature
from app.manual_daily_records.models import ManualDailyRecord
from app.kairon.models import KaironChartRecord, KaironUploadBatch
from app.roles.models import Role, RoleType, role_features
from app.roles.routes import _validate_role_scope
from app.users.models import Project, User
from app.users.schemas import validate_project_for_role


@pytest.fixture
def client_account(employee_user):
    token = uuid4().hex[:8]
    role = Role(title=f"Client-{token}", role_type=RoleType.query.filter_by(code="client").one())
    role.features = Feature.query.all()
    db.session.add(role)
    db.session.flush()
    db.session.execute(role_features.update().where(role_features.c.role_id == role.id).values(can_read=True, can_write=False))
    client = User(email=f"client-{token}@example.test", first_name="Test", last_name="Client", emp_id=f"CL-{token}",
                  role_id=role.id, project_id=employee_user.project_id, password_hash=hash_password("test-password"), first_login=False, is_active=True)
    other_project = Project.query.filter(Project.id != employee_user.project_id).first()
    outside = User(email=f"outside-{token}@example.test", first_name="Outside", last_name="Project", emp_id=f"OT-{token}",
                   role_id=employee_user.role_id, project_id=other_project.id, password_hash=hash_password("test-password"), first_login=False, is_active=True)
    db.session.add_all([client, outside]); db.session.flush()
    for person in [employee_user, outside]:
        db.session.add(ManualDailyRecord(user_id=person.id, record_date=date(2026, 10, 12), production_count=7, pvp_count=7,
                      foundation_count=0, tech_issues_downtime_hours=Decimal(0), no_inventory_idle_time_hours=Decimal(0),
                      leave_hours=Decimal(0), meeting_engagement_hours=Decimal(0), status="pending"))
    batch = KaironUploadBatch(as_of_date=date(2026, 10, 12), source_filename="client-test.csv", uploaded_by_id=client.id)
    db.session.add(batch); db.session.flush()
    for person in [employee_user, outside]:
        db.session.add(KaironChartRecord(batch_id=batch.id, program="PVP", level="1LR", status="Completed", user_id=person.id,
                       coding_analyst_raw=person.first_name, created_date=date(2026, 10, 11), completed_date=date(2026, 10, 12)))
    db.session.commit()
    yield client, outside
    KaironChartRecord.query.filter_by(batch_id=batch.id).delete(synchronize_session=False)
    db.session.delete(batch)
    from app.sessions.models import Session
    Session.query.filter(Session.user_id.in_([client.id, outside.id])).delete(synchronize_session=False)
    ManualDailyRecord.query.filter(ManualDailyRecord.user_id.in_([employee_user.id, outside.id])).delete(synchronize_session=False)
    db.session.expire(role, ["features"])
    role.features = []
    db.session.delete(client); db.session.delete(outside); db.session.flush(); db.session.delete(role); db.session.commit()


def login(api_client, user):
    status, body = api_client.login(user.email, "test-password")
    assert status == 200, body


def test_client_project_scope_on_reports_dashboard_and_users(api_client, client_account, employee_user):
    client, outside = client_account
    login(api_client, client)
    status, body = api_client.get("/api/reports/manual?from=2026-10-12&to=2026-10-12")
    assert status == 200, body
    assert {r["userId"] for r in body["data"]["items"]} == {employee_user.id}
    status, body = api_client.get("/api/dashboards/coding?from=2026-10-12&to=2026-10-12")
    assert status == 200, body
    assert outside.id not in {row["userId"] for row in body["data"]}
    status, body = api_client.get("/api/reports/kairon/completed-users?completedDate=2026-10-12")
    assert status == 200, body
    assert {row["userId"] for row in body["data"]["items"]} == {employee_user.id}
    status, body = api_client.get(f"/api/reports/kairon/completed-records?completedDate=2026-10-12&userId={outside.id}")
    assert status == 200 and body["data"]["items"] == [], body
    status, body = api_client.get(f"/api/kairon/charts?userId={outside.id}")
    assert status == 200 and body["data"] == [], body
    status, body = api_client.get("/api/users/active")
    assert status == 200, body
    assert outside.id not in {row["id"] for row in body["data"]}
    status, body = api_client.get(f"/api/users/filter?projectIds={outside.project_id}")
    assert status == 200 and body["data"] == []
    status, body = api_client.get("/api/projects")
    assert status == 200, body
    assert {p["id"] for p in body["data"]} == {client.project_id}


def test_client_cannot_write_even_if_permission_is_corrupted(api_client, client_account):
    client, _ = client_account
    db.session.execute(role_features.update().where(role_features.c.role_id == client.role_id).values(can_write=True)); db.session.commit()
    assert not has_feature(client, "reports", "write")
    login(api_client, client)
    for path in ["/api/roles", "/api/users", "/api/manual-daily-records", "/api/lead-coverage", "/api/login-hours/uploads"]:
        status, body = api_client.post(path, {})
        assert status == 403, (path, body)
    status, body = api_client.get("/api/sessions/whoami")
    assert status == 200, body
    assert all(not p["canWrite"] for p in body["data"]["user"]["role"]["featurePermissions"])


def test_client_menu_allotment_and_project_required(api_client, client_account):
    client, _ = client_account
    validate_project_for_role(client.role, client.project_id)
    with pytest.raises(ValueError):
        validate_project_for_role(client.role, None)
    reports = Feature.query.filter_by(codename="reports").one()
    db.session.execute(role_features.delete().where(role_features.c.role_id == client.role_id, role_features.c.feature_id == reports.id)); db.session.commit()
    login(api_client, client)
    status, _ = api_client.get("/api/reports/manual")
    assert status == 403
    status, body = api_client.get("/api/team/client-members")
    assert status == 200, body
    assert all(set(member) == {"userId", "name", "reviewLevel"} for member in body["data"])
    assert api_client.get("/api/team/coders")[0] == 403
    with pytest.raises(HTTPException) as exc:
        _validate_role_scope({"role_type_id": client.role.role_type_id, "feature_permissions": [{"feature_id": reports.id, "can_write": True}]})
    assert exc.value.code == 422


def test_client_dashboard_review_levels_details_and_privacy(api_client, client_account, employee_user, lead_user):
    client, outside = client_account
    batch = KaironUploadBatch.query.filter_by(source_filename="client-test.csv").one()
    employee_record = ManualDailyRecord.query.filter_by(user_id=employee_user.id, record_date=date(2026,10,12)).one()
    employee_record.daily_target = 10
    employee_record.pvp_daily_target = 10
    employee_record.adjusted_cpd = Decimal("10.00")
    db.session.add(ManualDailyRecord(user_id=lead_user.id, record_date=date(2026,10,12), production_count=9,
        pvp_count=9, foundation_count=0, daily_target=90, pvp_daily_target=90, adjusted_cpd=Decimal("45.00"),
        tech_issues_downtime_hours=Decimal(0), no_inventory_idle_time_hours=Decimal(0),
        leave_hours=Decimal(4), meeting_engagement_hours=Decimal(0), status="approved"))
    # Wrong review levels must not inflate either scope or its goal/source records.
    for user, level in [(employee_user, "2LR"), (lead_user, "1LR"), (lead_user, "2LR")]:
        db.session.add(KaironChartRecord(batch_id=batch.id, program="PVP", level=level, status="Completed",
                       user_id=user.id, coding_analyst_raw=user.first_name, created_date=date(2026,10,11), completed_date=date(2026,10,12)))
    for user, level in [(employee_user, "1LR"), (employee_user, "2LR"), (lead_user, "2LR"), (lead_user, "1LR")]:
        db.session.add(KaironChartRecord(batch_id=batch.id, program="PVP", level=level, status="On Hold",
                       user_id=user.id, coding_analyst_raw=user.first_name, created_date=date(2026,9,1)))
    db.session.commit()
    login(api_client, client)
    status, body = api_client.get("/api/dashboards/client?month=2026-10")
    assert status == 200, body
    data = body["data"]
    assert data["coders"]["efficiency"]["kaironCharts"] == 1
    assert data["qa"]["efficiency"]["kaironCharts"] == 1
    assert data["coders"]["goal"]["completedCharts"] == 1
    assert data["qa"]["goal"]["completedCharts"] == 1
    assert data["coders"]["goal"]["to"] == "2026-10-31"
    assert outside.id not in {member["userId"] for member in data["members"]}
    overview = data["overview"]
    assert overview["efficiency"]["kaironCharts"] == 2
    assert overview["efficiency"]["manualCharts"] == 16
    assert Decimal(overview["efficiency"]["manualEfficiencyPercent"]) == Decimal("29.1")
    assert Decimal(overview["efficiency"]["manualCpd"]) == Decimal("10.7")
    assert Decimal(overview["efficiency"]["kaironCpd"]) == Decimal("1.3")
    assert overview["goal"]["calendarWorkingDays"] == data["qa"]["goal"]["calendarWorkingDays"]
    for key in ("userCount", "completedCharts", "manualCharts", "targetCharts", "eligibleDays", "leaveDaysExcluded"):
        assert overview["goal"][key] == data["qa"]["goal"][key] + data["coders"]["goal"][key]
    assert Decimal(overview["goal"]["adjustedTargetCharts"]) == (Decimal(data["qa"]["goal"]["adjustedTargetCharts"]) + Decimal(data["coders"]["goal"]["adjustedTargetCharts"]))
    private = {"stage", "insideMinutes", "loginDays", "productiveMinutes", "empId", "leadId", "teams", "cohortOptions", "email", "reviewedById", "reviewedAt"}
    def check(value):
        if isinstance(value, dict):
            assert not private.intersection(value), value
            for item in value.values(): check(item)
        elif isinstance(value, list):
            for item in value: check(item)
    check(data)
    for person, level in [(employee_user, "1LR"), (lead_user, "2LR")]:
        status, body = api_client.get(f"/api/dashboards/client?month=2026-10&userId={person.id}")
        assert status == 200, body
        assert {member["userId"] for member in body["data"]["members"]} == {person.id}
        assert len(body["data"]["charts"]) == 1
        assert {chart["level"] for chart in body["data"]["charts"]} == {level}
        assert len(body["data"]["holds"]) == 1
        assert {chart["level"] for chart in body["data"]["holds"]} == {level}
        check(body["data"])
    status, body = api_client.get(f"/api/dashboards/client?month=2026-10&userId={outside.id}")
    assert status == 404, body
    status, body = api_client.get("/api/dashboards/client?from=2026-10-12&to=2026-10-01")
    assert status == 400
    for path in ["/api/dashboards/coding?month=2026-10&includeDaily=true", "/api/reports/project-overview?month=2026-10&includeDaily=true"]:
        status, body = api_client.get(path)
        assert status == 200, body
        check(body["data"])

    holds_feature = Feature.query.filter_by(codename="chart_holds").one()
    db.session.execute(role_features.update().where(role_features.c.role_id == client.role_id,
        role_features.c.feature_id == holds_feature.id).values(can_read=False, can_write=False))
    db.session.commit()
    status, body = api_client.get(f"/api/dashboards/client?month=2026-10&userId={employee_user.id}")
    assert status == 200 and "holds" not in body["data"], body


def test_client_dashboard_requires_allotted_dashboard(api_client, client_account):
    client, _ = client_account
    feature = Feature.query.filter_by(codename="dashboard").one()
    db.session.execute(role_features.update().where(role_features.c.role_id == client.role_id,
        role_features.c.feature_id == feature.id).values(can_read=False, can_write=False))
    db.session.commit()
    login(api_client, client)
    status, body = api_client.get("/api/dashboards/client")
    assert status == 403, body
