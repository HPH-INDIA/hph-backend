import pytest
from app.extensions import db
from app.users.models import User
from app.roles.models import Role, RoleType
from app.encryption.passwords import hash_password

@pytest.mark.parametrize("path", ["/api/users", "/api/users/active", "/api/users/inactive", "/api/users/filter"])
def test_superadmin_hidden_from_every_list(api_client, manager_user, path):
    role = Role.query.join(RoleType).filter(RoleType.code == "super_admin").first()
    user = User.query.filter_by(email="hidden-superadmin-test@example.com").first()
    if user is None:
        user = User(email="hidden-superadmin-test@example.com", first_name="Hidden", last_name="Admin", emp_id="HIDDEN-SUPERADMIN-TEST", role_id=role.id, password_hash=hash_password("test-password"), first_login=False, is_active=False)
        db.session.add(user)
        db.session.commit()
    assert api_client.login(manager_user.email, "test-password")[0] == 200
    status, body = api_client.get(path)
    assert status == 200, body
    assert all(row["role"]["roleType"] != "super_admin" for row in body["data"])
    if path.endswith("filter"):
        status, body = api_client.get(f"{path}?roleIds={role.id}")
        assert status == 200, body
        assert body["data"] == []
