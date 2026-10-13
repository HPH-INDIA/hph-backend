from app.auth import has_feature
from app.extensions import db
from app.features.models import Feature
from app.roles.models import role_features
from app.roles.models import Role, RoleType


def test_read_only_feature_allows_reads_and_rejects_writes(api_client, manager_user):
    feature = Feature.query.filter_by(codename="team").one()
    assignment = db.session.execute(
        db.select(role_features.c.can_read, role_features.c.can_write).where(
            role_features.c.role_id == manager_user.role_id,
            role_features.c.feature_id == feature.id,
        )
    ).one()

    try:
        db.session.execute(
            role_features.update()
            .where(
                role_features.c.role_id == manager_user.role_id,
                role_features.c.feature_id == feature.id,
            )
            .values(can_read=True, can_write=False)
        )
        db.session.commit()
        assert has_feature(manager_user, "team", "read")
        assert not has_feature(manager_user, "team", "write")

        status, body = api_client.login("test-manager@example.com", "test-password")
        assert status == 200, body

        status, body = api_client.get("/api/teams/mine")
        assert status == 200, body

        status, body = api_client.post(
            "/api/teams/mine/assign",
            {"coderIds": [manager_user.id], "leadId": manager_user.id},
        )
        assert status == 403
        assert "write access" in body["message"]
    finally:
        db.session.execute(
            role_features.update()
            .where(
                role_features.c.role_id == manager_user.role_id,
                role_features.c.feature_id == feature.id,
            )
            .values(can_read=assignment.can_read, can_write=assignment.can_write)
        )
        db.session.commit()


def test_dashboard_and_reports_are_consolidated_by_role():
    feature_codenames = {feature.codename for feature in Feature.query.all()}
    assert "dashboard" in feature_codenames
    assert "reports" in feature_codenames
    assert "coding_project_dashboard" not in feature_codenames
    assert "kairon_management" not in feature_codenames
    assert "manual_daily_records_management" not in feature_codenames

    reports = Feature.query.filter_by(codename="reports").one()
    permissions = dict(
        db.session.execute(
            db.select(RoleType.code, role_features.c.can_write)
            .select_from(
                role_features
                .join(Role, Role.id == role_features.c.role_id)
                .join(RoleType, RoleType.id == Role.role_type_id)
            )
            .where(role_features.c.feature_id == reports.id)
        ).all()
    )
    assert permissions == {
        "super_admin": False,
        "admin": False,
        "manager": True,
        "lead": True,
        "employee": True,
    }


def test_login_hours_uses_read_and_write_permissions_on_one_feature():
    feature_codenames = {feature.codename for feature in Feature.query.all()}
    assert "login_hours" in feature_codenames
    assert "login_hours_management" not in feature_codenames

    login_hours = Feature.query.filter_by(codename="login_hours").one()
    permissions = dict(
        db.session.execute(
            db.select(RoleType.code, role_features.c.can_write)
            .select_from(
                role_features
                .join(Role, Role.id == role_features.c.role_id)
                .join(RoleType, RoleType.id == Role.role_type_id)
            )
            .where(role_features.c.feature_id == login_hours.id)
        ).all()
    )
    assert permissions == {
        "manager": True,
        "lead": False,
        "employee": False,
    }
