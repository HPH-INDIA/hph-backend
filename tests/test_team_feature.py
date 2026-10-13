from app.extensions import db
from app.features.models import Feature
from app.roles.models import role_features
from app.roles.schemas import RoleSchema
from app.sessions.schemas import RoleProfileSchema


def test_catalog_and_role_profile_expose_only_team(api_client, employee_user):
    assert api_client.login("superadmin", "superadmin")[0] == 200
    status, body = api_client.get("/api/features")
    assert status == 200, body
    codes = [feature["codename"] for feature in body["data"]]
    assert codes.count("team") == 1
    assert "stage_view" not in codes
    profile = RoleProfileSchema().dump(employee_user.role)
    assert "team" in profile["features"] and "stage_view" not in profile["features"]
    team = Feature.query.filter_by(codename="team").one()
    legacy = Feature.query.filter_by(codename="stage_view").one()
    role = RoleSchema().dump(employee_user.role)
    assert team.id in role["features"] and legacy.id not in role["features"]
    assert next(p for p in role["feature_permissions"] if p["feature_id"] == team.id) == {
        "feature_id": team.id, "can_read": True, "can_write": False}


def test_team_grant_alone_supports_coder_stages_without_administration(api_client, employee_user):
    role = employee_user.role
    team = Feature.query.filter_by(codename="team").one()
    legacy = Feature.query.filter_by(codename="stage_view").one()
    originals = db.session.execute(db.select(role_features).where(role_features.c.role_id == role.id,
        role_features.c.feature_id.in_([team.id, legacy.id]))).mappings().all()
    db.session.execute(role_features.delete().where(role_features.c.role_id == role.id,
        role_features.c.feature_id.in_([team.id, legacy.id])))
    db.session.execute(role_features.insert().values(role_id=role.id, feature_id=team.id, can_read=True, can_write=False))
    db.session.commit()
    try:
        assert api_client.login(employee_user.email, "test-password")[0] == 200
        status, body = api_client.get("/api/team/coders")
        assert status == 200, body
        assert all(row["coder"]["id"] == employee_user.id for row in body["data"]["items"])
        assert api_client.get("/api/lead-coverage")[0] == 403
        assert api_client.get("/api/team/cohorts")[0] == 403
        assert api_client.post("/api/teams/mine/assign", {})[0] == 403
    finally:
        db.session.execute(role_features.delete().where(role_features.c.role_id == role.id,
            role_features.c.feature_id.in_([team.id, legacy.id])))
        for row in originals:
            db.session.execute(role_features.insert().values(dict(row)))
        db.session.commit()
