import datetime as dt

from app.extensions import db
from app.encryption.passwords import hash_password, verify_password
from app.roles.models import Role, RoleType
from app.sessions.models import Session
from app.users.models import Project, User


def test_user_can_reset_password_and_other_sessions_are_revoked(
    api_client, manager_user, monkeypatch
):
    original_password_hash = manager_user.password_hash
    monkeypatch.setattr("app.users.routes.send_password_changed_confirmation", lambda user: None)

    status, _ = api_client.login(manager_user.email, "test-password")
    assert status == 200
    status, _ = api_client.login(manager_user.email, "test-password")
    assert status == 200
    assert Session.query.filter_by(user_id=manager_user.id, revoked_at=None).count() >= 2

    status, body = api_client.post(
        "/api/users/set-password",
        {
            "current_password": "test-password",
            "new_password": "new-secure-password",
            "new_password_confirm": "new-secure-password",
        },
    )

    assert status == 200, body
    assert body["message"] == "Password updated successfully."
    assert Session.query.filter_by(user_id=manager_user.id, revoked_at=None).count() == 1
    verify_password(manager_user.password_hash, "new-secure-password")

    manager_user.password_hash = original_password_hash
    db.session.commit()


def test_deactivate_user_persists_and_returns_last_working_day(api_client, manager_user, employee_user):
    employee_user.is_active = True
    employee_user.last_working_day = None
    db.session.commit()
    api_client.login(manager_user.email, "test-password")

    status, body = api_client.delete(
        f"/api/users/{employee_user.id}", {"last_working_day": "2026-09-15"}
    )

    assert status == 200, body
    db.session.refresh(employee_user)
    assert employee_user.is_active is False
    assert employee_user.last_working_day == dt.date(2026, 9, 15)

    status, body = api_client.get("/api/users/inactive")
    assert status == 200, body
    serialized = next(row for row in body["data"] if row["id"] == employee_user.id)
    assert serialized["last_working_day"] == "2026-09-15"

    employee_user.is_active = True
    employee_user.last_working_day = None
    db.session.commit()


def test_filter_users_by_role_id(api_client, manager_user, employee_user):
    status, _ = api_client.login("test-manager@example.com", "test-password")
    assert status == 200

    manager_role_id = manager_user.role_id
    status, body = api_client.get(f"/api/users/filter?roleIds={manager_role_id}")
    assert status == 200, body
    returned_ids = {row["id"] for row in body["data"]}
    assert manager_user.id in returned_ids
    assert employee_user.id not in returned_ids


def test_filter_users_by_project_and_role_id_ands_them(api_client, manager_user, employee_user):
    project = Project.query.filter_by(name="TEST-FILTER-PROJECT").first()
    if project is None:
        project = Project(name="TEST-FILTER-PROJECT")
        db.session.add(project)
        db.session.commit()

    manager_user.project_id = project.id
    db.session.commit()

    status, _ = api_client.login("test-manager@example.com", "test-password")
    assert status == 200

    employee_role_id = employee_user.role_id
    status, body = api_client.get(
        f"/api/users/filter?projectIds={project.id}&roleIds={employee_role_id}"
    )
    assert status == 200, body
    # manager_user matches the project but not the role; employee_user matches
    # the role but not the project - AND semantics means neither is returned.
    returned_ids = {row["id"] for row in body["data"]}
    assert manager_user.id not in returned_ids
    assert employee_user.id not in returned_ids

    status, body = api_client.get(f"/api/users/filter?projectIds={project.id}")
    assert status == 200, body
    returned_ids = {row["id"] for row in body["data"]}
    assert manager_user.id in returned_ids


def test_manager_team_is_paginated_and_bulk_assignment_requires_own_lead(api_client, manager_user):
    project = Project.query.filter_by(name="TEST-TEAM-ASSIGNMENT").first() or Project(name="TEST-TEAM-ASSIGNMENT")
    db.session.add(project)
    db.session.flush()
    lead_role = Role.query.filter_by(role_type_id=RoleType.query.filter_by(code="lead").one().id).first()
    employee_role = Role.query.filter_by(role_type_id=RoleType.query.filter_by(code="employee").one().id).first()

    def user(email, emp_id, role, first_name):
        existing = User.query.filter_by(email=email).first()
        if existing is not None:
            return existing
        created = User(
            email=email,
            first_name=first_name,
            last_name="Team Test",
            emp_id=emp_id,
            role_id=role.id,
            password_hash=hash_password("test-password"),
            first_login=False,
            is_active=True,
        )
        db.session.add(created)
        db.session.flush()
        return created

    lead = user("team-lead@example.com", "TEST-TEAM-LEAD", lead_role, "Lead")
    coders = [
        user(f"team-coder-{number}@example.com", f"TEST-TEAM-CODER-{number}", employee_role, f"Coder {number}")
        for number in range(1, 4)
    ]
    manager_user.project_id = project.id
    lead.project_id = project.id
    lead.reports_to_id = manager_user.id
    for coder in coders:
        coder.project_id = project.id
        coder.reports_to_id = None
    db.session.commit()

    api_client.login("test-manager@example.com", "test-password")
    status, body = api_client.get("/api/teams/mine?page=1&pageSize=2")
    assert status == 200, body
    assert body["data"]["coders"]["total"] == 3
    assert len(body["data"]["coders"]["items"]) == 2
    assert body["data"]["unassignedCount"] == 3

    status, body = api_client.post(
        "/api/teams/mine/assign",
        {"coderIds": [coders[0].id, coders[1].id], "leadId": lead.id},
    )
    assert status == 200, body
    assert {row["id"] for row in body["data"]} == {coders[0].id, coders[1].id}

    status, body = api_client.get(f"/api/teams/mine?leadId={lead.id}")
    assert status == 200, body
    assert body["data"]["coders"]["total"] == 2
    assert body["data"]["leads"][0]["coderCount"] == 2

    status, _ = api_client.post(
        "/api/teams/mine/assign",
        {"coderIds": [coders[2].id], "leadId": coders[0].id},
    )
    assert status == 400

    manager_user.project_id = None
    lead.project_id = None
    lead.reports_to_id = None
    for coder in coders:
        coder.project_id = None
        coder.reports_to_id = None
    db.session.commit()
