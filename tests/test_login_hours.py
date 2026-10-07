import base64
from datetime import date
from io import BytesIO

from openpyxl import Workbook
import pytest

from app.encryption.passwords import hash_password
from app.cohorts.models import Cohort, CohortMembership
from app.extensions import db
from app.login_hours.models import LoginHourRecord
from app.login_hours.services import _readable_user_ids, list_login_hour_records
from app.roles.models import Role, RoleType, role_features
from app.features.models import Feature
from app.users.models import Project, User


HEADERS = [
    "Date", "Personnel ID", "Employee", "Department", "First In", "Last Out",
    "Total Inside", "Total Outside", "Total Span", "Entries", "Exits", "Status", "Anomalies",
]


@pytest.mark.parametrize("role_code", ["admin", "lead", "employee"])
def test_upload_requires_explicit_write_permission(api_client, role_code):
    admin = _coding_role_user(
        "Attendance Writer", f"attendance-writer-{role_code}@example.com", f"TEST-LH-WRITER-{role_code}", role_code
    )
    feature = Feature.query.filter_by(codename="login_hours").one()
    condition = (role_features.c.role_id == admin.role_id) & (role_features.c.feature_id == feature.id)
    original = db.session.execute(db.select(role_features).where(condition)).mappings().first()
    try:
        db.session.execute(role_features.delete().where(condition))
        db.session.execute(role_features.insert().values(role_id=admin.role_id, feature_id=feature.id, can_read=True, can_write=False))
        db.session.commit()
        api_client.login(admin.email, "test-password")
        payload = _workbook_base64("03_All_Employees", 3, [
            ["2026-09-15", 9999, "Unknown Person", "Coding", "09:00", "17:00", "8h 0m", "0h 0m", "8h 0m", 1, 1, "Complete", 0],
        ])
        request = {"sourceFilename": "attendance.xlsx", "fileBase64": payload}
        status, body = api_client.post("/api/login-hours/uploads", request)
        assert status == 403, body
        db.session.execute(role_features.update().where(condition).values(can_write=True))
        db.session.commit()
        status, body = api_client.post("/api/login-hours/uploads", request)
        assert status == 201, body
    finally:
        db.session.execute(role_features.delete().where(condition))
        if original:
            db.session.execute(role_features.insert().values(**dict(original)))
        db.session.commit()


def _coding_user(name, email, emp_id):
    first_name, last_name = name.split(" ", 1)
    user = User.query.filter_by(first_name=first_name, last_name=last_name).first()
    if user is None:
        role_type = RoleType.query.filter_by(code="employee").one()
        role = Role.query.filter_by(role_type_id=role_type.id).first()
        project = Project.query.filter_by(name="CODING").one()
        user = User(
            email=email,
            first_name=first_name,
            last_name=last_name,
            emp_id=emp_id,
            role_id=role.id,
            project_id=project.id,
            password_hash=hash_password("test-password"),
            first_login=False,
            is_active=True,
        )
        db.session.add(user)
        db.session.commit()
    return user


def _coding_role_user(name, email, emp_id, role_code, reports_to_id=None):
    first_name, last_name = name.split(" ", 1)
    role_type = RoleType.query.filter_by(code=role_code).one()
    role = Role.query.filter_by(role_type_id=role_type.id).first()
    project = Project.query.filter_by(name="CODING").one()
    user = User.query.filter_by(email=email).first()
    if user is None:
        user = User(
            email=email,
            first_name=first_name,
            last_name=last_name,
            emp_id=emp_id,
            role_id=role.id,
            project_id=project.id,
            reports_to_id=reports_to_id,
            password_hash=hash_password("test-password"),
            first_login=False,
            is_active=True,
        )
        db.session.add(user)
    else:
        user.first_name = first_name
        user.last_name = last_name
        user.role_id = role.id
        user.project_id = project.id
        user.reports_to_id = reports_to_id
        user.is_active = True
    db.session.commit()
    return user


def _workbook_base64(sheet_name, header_row, rows):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name
    for _ in range(header_row - 1):
        sheet.append(["Attendance report"])
    sheet.append(HEADERS)
    for row in rows:
        sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    return base64.b64encode(output.getvalue()).decode("ascii")


def test_admin_all_projects_and_manager_project_scope(manager_user):
    admin = _coding_role_user("Projects Admin", "projects-admin@example.com", "TEST-LH-PROJECT-ADMIN", "admin")
    colleague = _coding_role_user("Project Colleague", "project-colleague@example.com", "TEST-LH-COLLEAGUE", "employee")
    outsider = _coding_role_user("Other Project", "other-project@example.com", "TEST-LH-OTHER", "employee")
    project = Project.query.filter_by(name="ATTENDANCE-TEST").first()
    if project is None:
        project = Project(name="ATTENDANCE-TEST")
        db.session.add(project)
        db.session.flush()
    original_project = outsider.project_id
    try:
        outsider.project_id = project.id
        db.session.commit()
        assert colleague.id in _readable_user_ids(manager_user)
        assert outsider.id not in _readable_user_ids(manager_user)
        assert outsider.id in _readable_user_ids(admin)
        result = list_login_hour_records(admin, 1, 25, project_id=project.id)
        assert project.id in {option["id"] for option in result["filter_options"]["projects"]}
    finally:
        outsider.project_id = original_project
        db.session.commit()


def test_manager_uploads_office_dashboard_and_unmatched_names_are_dropped(api_client, manager_user):
    user = _coding_user("Ajaya Mishra", "login-hours-ajaya@example.com", "TEST-LH-AJAYA")
    payload = _workbook_base64(
        "03_All_Employees",
        3,
        [
            ["2026-09-15", 1034, "Ajay Mishra", "Coding", "08:43", "17:34", "7h 16m", "1h 34m", "8h 51m", 5, 5, "Complete", 0],
            ["2026-09-15", 9999, "Unknown Person", "Coding", "09:00", "17:00", "8h 0m", "0h 0m", "8h 0m", 1, 1, "Complete", 0],
            ["2026-09-15", 3001, "Non Coding Person", "RCM", "09:00", "17:00", "8h 0m", "0h 0m", "8h 0m", 1, 1, "Complete", 0],
        ],
    )

    api_client.login("test-manager@example.com", "test-password")
    status, body = api_client.post(
        "/api/login-hours/uploads",
        {"sourceFilename": "office.xlsx", "fileBase64": payload},
    )

    assert status == 201, body
    assert body["data"]["sourceFormat"] == "Office attendance dashboard"
    assert body["data"]["matchedCount"] == 1
    assert body["data"]["unmatchedCount"] == 1
    assert body["data"]["unmatchedNames"] == ["Unknown Person"]
    record = LoginHourRecord.query.one()
    assert record.user_id == user.id
    assert record.employee_name_raw == "Ajay Mishra"
    assert record.total_inside_minutes == 436
    assert record.total_span_minutes == 531


def test_manager_uploads_employee_wise_format(api_client, manager_user):
    user = _coding_user("Login Hours", "login-hours-exact@example.com", "TEST-LH-EXACT")
    payload = _workbook_base64(
        "Coding",
        1,
        [["2026-09-16", 2001, "Login Hours", "Coding", "09:00", "17:30", "7h 45m", "0h 45m", "8h 30m", 2, 2, "Complete", 0]],
    )

    api_client.login("test-manager@example.com", "test-password")
    status, body = api_client.post(
        "/api/login-hours/uploads",
        {"sourceFilename": "employee-wise.xlsx", "fileBase64": payload},
    )

    assert status == 201, body
    assert body["data"]["sourceFormat"] == "Employee-wise attendance"
    assert body["data"]["matchedCount"] == 1
    record = LoginHourRecord.query.one()
    assert record.user_id == user.id
    assert record.total_inside_minutes == 465


def test_lead_and_employee_record_access_is_limited_to_their_reporting_scope(api_client, manager_user):
    lead = _coding_role_user(
        "Scope Lead", "login-hours-scope-lead@example.com", "TEST-LH-SCOPE-LEAD", "lead", manager_user.id
    )
    team_member = _coding_role_user(
        "Scope Member", "login-hours-scope-member@example.com", "TEST-LH-SCOPE-MEMBER", "employee", lead.id
    )
    outside_member = _coding_role_user(
        "Outside Member", "login-hours-outside-member@example.com", "TEST-LH-OUTSIDE", "employee"
    )
    payload = _workbook_base64(
        "Coding",
        1,
        [
            ["2026-09-17", 3001, "Scope Lead", "Coding", "09:00", "17:00", "8h 0m", "0h 0m", "8h 0m", 1, 1, "Complete", 0],
            ["2026-09-17", 3002, "Scope Member", "Coding", "09:00", "17:00", "8h 0m", "0h 0m", "8h 0m", 1, 1, "Complete", 0],
            ["2026-09-17", 3003, "Outside Member", "Coding", "09:00", "17:00", "8h 0m", "0h 0m", "8h 0m", 1, 1, "Complete", 0],
        ],
    )

    api_client.login("test-manager@example.com", "test-password")
    status, body = api_client.post(
        "/api/login-hours/uploads",
        {"sourceFilename": "scope.xlsx", "fileBase64": payload},
    )
    assert status == 201, body

    cohort = Cohort(sequence_no=99, label="Login Hours Cohort", window_start=date(2026, 9, 1))
    db.session.add(cohort)
    db.session.flush()
    db.session.add(
        CohortMembership(
            cohort_id=cohort.id,
            user_id=team_member.id,
            joined_on=date(2026, 9, 1),
            assigned_by_id=manager_user.id,
        )
    )
    db.session.commit()

    status, body = api_client.get(f"/api/login-hours/records?leadId={lead.id}")
    assert status == 200, body
    assert {item["userId"] for item in body["data"]["items"]} == {lead.id, team_member.id}
    assert body["data"]["averageInsideMinutes"] == 480.0
    assert {option["id"] for option in body["data"]["filterOptions"]["users"]} >= {lead.id, team_member.id}
    assert lead.id in {option["id"] for option in body["data"]["filterOptions"]["leads"]}

    status, body = api_client.get(f"/api/login-hours/records?cohortId={cohort.id}")
    assert status == 200, body
    assert [item["userId"] for item in body["data"]["items"]] == [team_member.id]

    api_client.login(lead.email, "test-password")
    status, body = api_client.get("/api/login-hours/records")
    assert status == 200, body
    assert {item["userId"] for item in body["data"]["items"]} == {lead.id, team_member.id}
    assert outside_member.id not in {item["userId"] for item in body["data"]["items"]}

    api_client.login(team_member.email, "test-password")
    status, body = api_client.get("/api/login-hours/records")
    assert status == 200, body
    assert {item["userId"] for item in body["data"]["items"]} == {lead.id, team_member.id}

    status, body = api_client.get(f"/api/login-hours/records?userIds={lead.id}&userIds={team_member.id}&from=2026-09-17&to=2026-09-17")
    assert status == 200, body
    assert body["data"]["total"] == 2

    status, body = api_client.get("/api/login-hours/records?from=2026-09-18&to=2026-09-17")
    assert status == 400, body

    status, body = api_client.get(f"/api/login-hours/records?userId={lead.id}")
    assert status == 200, body

    status, body = api_client.get(f"/api/login-hours/records?userIds={lead.id}&userIds={outside_member.id}")
    assert status == 403, body

    status, body = api_client.post(
        "/api/login-hours/uploads",
        {"sourceFilename": "forbidden.xlsx", "fileBase64": payload},
    )
    assert status == 403, body


def test_chunked_attendance_retry_order_and_ownership(api_client, manager_user, employee_user):
    import uuid
    from openpyxl.utils.datetime import to_excel
    api_client.login(manager_user.email, "test-password")
    request = {"sourceFilename": "chunks.xlsx", "sourceFormat": "Employee-wise attendance", "uploadId": str(uuid.uuid4()),
        "chunkIndex": 0, "headers": HEADERS, "rows": [
        [to_excel(date(2026, 10, 1)), "P1", f"{employee_user.first_name} {employee_user.last_name}", "Coding", 9 / 24, 17 / 24, 8 / 24, 0, 8 / 24, 1, 1, "Complete", 0],
        ["2026-10-01", "P2", "Unknown Person", "Coding", "09:00", "17:00", "8h", "0h", "8h", 1, 1, "Complete", 0]]}
    status, body = api_client.post("/api/login-hours/uploads/records", request)
    assert status == 201, body
    batch_id = body["data"]["id"]
    assert body["data"]["matchedCount"] == 1
    assert body["data"]["unmatchedCount"] == 1
    record = LoginHourRecord.query.filter_by(user_id=employee_user.id, attendance_date=date(2026, 10, 1)).one()
    assert record.total_inside_minutes == 480
    assert record.first_in.hour == 9
    # Losing the first response is safe: the UUID retrieves the same batch.
    status, retry = api_client.post("/api/login-hours/uploads/records", request)
    assert status == 201, retry
    assert retry["data"]["id"] == batch_id
    assert retry["data"]["rowCount"] == 2
    changed = {**request, "rows": request["rows"][:1]}
    assert api_client.post("/api/login-hours/uploads/records", changed)[0] == 409
    assert api_client.post("/api/login-hours/uploads/records", {**request, "batchId": batch_id, "chunkIndex": 2})[0] == 409
    second = {**request, "batchId": batch_id, "chunkIndex": 1, "rows": [["2026-10-02", "P1", f"{employee_user.first_name} {employee_user.last_name}", "Coding", "09:00", "17:00", "7h", "0h", "8h", 1, 1, "Complete", 0]]}
    status, body = api_client.post("/api/login-hours/uploads/records", second)
    assert status == 201, body
    assert body["data"]["matchedCount"] == 2
    assert body["data"]["unmatchedNames"] == ["Unknown Person"]
    assert api_client.post("/api/login-hours/uploads/records", {**request, "rows": request["rows"] * 101})[0] == 422
    # Unmatched-only chunks are valid, and reads cannot grant upload access.
    api_client.login(employee_user.email, "test-password")
    assert api_client.post("/api/login-hours/uploads/records", second)[0] == 403
