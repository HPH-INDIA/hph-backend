import pytest
from app.extensions import db
from app.features.models import Feature
from app.roles.models import role_features
from app.users.models import Project

@pytest.fixture
def project_permission(employee_user):
    feature = Feature.query.filter_by(codename="project_management").one()
    def grant(write=False):
        db.session.execute(role_features.delete().where(role_features.c.role_id == employee_user.role_id, role_features.c.feature_id == feature.id))
        db.session.execute(role_features.insert().values(role_id=employee_user.role_id, feature_id=feature.id, can_read=True, can_write=write))
        db.session.commit()
    yield grant
    db.session.execute(role_features.delete().where(role_features.c.role_id == employee_user.role_id, role_features.c.feature_id == feature.id))
    for project in Project.query.filter(Project.name.like("PROJECT-TEST-%")).all():
        db.session.delete(project)
    db.session.commit()

def test_project_read_write_permissions(api_client, employee_user, project_permission):
    assert api_client.login(employee_user.email, "test-password")[0] == 200
    assert api_client.get("/api/projects")[0] == 403
    assert api_client.get("/api/projects/options")[0] == 403
    project_permission()
    assert api_client.get("/api/projects")[0] == 200
    assert api_client.get("/api/projects/options")[0] == 200
    assert api_client.post("/api/projects", {"name": "PROJECT-TEST-PERMISSION"})[0] == 403
    coding = Project.query.filter_by(name="CODING").one()
    assert api_client.patch(f"/api/projects/{coding.id}", {"name": "changed"})[0] == 403
    assert api_client.delete(f"/api/projects/{coding.id}")[0] == 403
    project_permission(write=True)
    status, body = api_client.post("/api/projects", {"name": "  PROJECT-TEST-CREATE  "})
    assert status == 201, body
    project_id = body["data"]["id"]
    assert body["data"]["name"] == "PROJECT-TEST-CREATE"
    assert api_client.post("/api/projects", {"name": "project-test-create"})[0] == 409
    assert api_client.post("/api/projects", {"name": "   "})[0] == 422
    assert api_client.post("/api/projects", {"name": "X" * 65})[0] == 422
    assert api_client.patch(f"/api/projects/{project_id}", {"name": "PROJECT-TEST-RENAMED"})[0] == 200
    assert api_client.patch(f"/api/projects/{coding.id}", {"name": "changed"})[0] == 409
    assert api_client.delete(f"/api/projects/{coding.id}")[0] == 409
    assert api_client.delete(f"/api/projects/{project_id}")[0] == 200
    assert api_client.delete(f"/api/projects/{project_id}")[0] == 404

def test_assigned_custom_project_cannot_be_deleted(api_client, employee_user, project_permission):
    project_permission(write=True)
    api_client.login(employee_user.email, "test-password")
    status, body = api_client.post("/api/projects", {"name": "PROJECT-TEST-ASSIGNED"})
    assert status == 201, body
    project_id = body["data"]["id"]
    previous = employee_user.project_id
    try:
        employee_user.project_id = project_id
        db.session.commit()
        status, body = api_client.get("/api/projects")
        assert status == 200
        assert next(p for p in body["data"] if p["id"] == project_id)["user_count"] == 1
        assert api_client.delete(f"/api/projects/{project_id}")[0] == 409
        assert api_client.patch(f"/api/projects/{project_id}", {"name": "PROJECT-TEST-ASSIGNED-RENAMED"})[0] == 200
        db.session.refresh(employee_user)
        assert employee_user.project_id == project_id
    finally:
        employee_user.project_id = previous
        db.session.commit()

def test_user_management_can_load_options_without_project_management(api_client, manager_user):
    assert api_client.login(manager_user.email, "test-password")[0] == 200
    status, body = api_client.get("/api/projects/options")
    assert status == 200, body
    assert any(p["name"] == "CODING" for p in body["data"])
    assert all("user_count" not in p for p in body["data"])
    assert api_client.get("/api/projects")[0] == 403
