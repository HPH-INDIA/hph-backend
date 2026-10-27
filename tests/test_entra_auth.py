"""Real database/session/crypto integration; Microsoft HTTP is replaced at its boundary."""
import json
import secrets
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa

from app.encryption.crypto import encrypt_payload
from app.encryption.services import get_active_key
from app.entra.models import AuthTransaction, ExternalIdentity, IdentityAudit
from app.entra.services import link_identity, safe_return_to
from app.extensions import db
from app.roles.models import Role, RoleType
from app.sessions.models import Session
from app.users.models import Project, User

TENANT = "11111111-1111-4111-8111-111111111111"
CLIENT = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def entra(app, monkeypatch, employee_user):
    import app.entra.routes as routes
    import app.entra.services as services
    values = dict(ENTRA_ENABLED=True, ENTRA_TENANT_ID=TENANT, ENTRA_CLIENT_ID=CLIENT,
        ENTRA_TRANSACTION_KEY=Fernet.generate_key().decode(), PUBLIC_APP_ORIGIN="http://localhost:5173",
        ENTRA_CLIENT_SECRET="test-only", SESSION_COOKIE_SECURE=False, CSRF_ENABLED=True)
    for key, value in values.items():
        monkeypatch.setitem(app.config, key, value)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    fake_keys = SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=private.public_key()))
    monkeypatch.setattr(services, "signing_keys", lambda tenant: fake_keys)
    state = {"oid": str(uuid4()), "claims": {}, "error": None, "exchanges": 0}

    class Microsoft:
        def initiate_auth_code_flow(self, **kwargs):
            assert kwargs["scopes"] == []
            assert kwargs["prompt"] == "select_account"
            assert kwargs["response_mode"] == "query"
            state["flow"] = {"state": secrets.token_urlsafe(32), "nonce": "unit-nonce", "code_verifier": "private-verifier"}
            return {**state["flow"], "auth_uri": "https://login.microsoftonline.com/test/authorize"}

        def acquire_token_by_auth_code_flow(self, flow, response):
            state["exchanges"] += 1
            assert flow["state"] == response["state"]
            if state["error"]:
                raise state["error"]
            now = datetime.now(timezone.utc)
            claims = {"aud": CLIENT, "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
                "tid": TENANT, "oid": state["oid"], "nonce": "unit-nonce", "iat": now,
                "nbf": now, "exp": now + timedelta(minutes=5), **state["claims"]}
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048) if state.get("bad_signature") else private
            return {"id_token": jwt.encode(claims, key, algorithm="RS256")}

    monkeypatch.setattr(routes, "client", lambda: Microsoft())
    link_identity(employee_user, state["oid"], "member", operator="pytest")
    db.session.commit()
    yield state
    # Restore shared fixtures and remove all auth test data, including revocations.
    db.session.rollback()
    db.session.execute(db.delete(AuthTransaction))
    db.session.execute(db.delete(IdentityAudit))
    db.session.execute(db.delete(ExternalIdentity))
    employee_user.is_active = True
    employee_user.first_login = False
    employee_user.temp_password_expires_at = None
    db.session.commit()


def begin(api_client, entra, destination="/reports?tab=manual"):
    response = api_client.client.get("/auth/login", query_string={"return_to": destination})
    assert response.status_code == 302
    row = AuthTransaction.query.one()
    assert "private-verifier" not in row.encrypted_flow
    assert entra["flow"]["state"] not in row.state_hash
    return entra["flow"]["state"]


def finish(api_client, state):
    return api_client.client.get("/auth/callback", query_string={"state": state, "code": "test-code"})


def error(response):
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    return parse_qs(urlsplit(response.location).query)["auth_error"][0]


def test_disabled_entra_preserves_password_login(api_client, employee_user):
    assert api_client.client.get("/auth/options").json == {"microsoftEnabled": False}
    assert error(api_client.client.get("/auth/login")) == "microsoft_unavailable"
    assert api_client.login(employee_user.email, "test-password")[0] == 200


def test_both_methods_share_user_and_keep_password(api_client, entra, employee_user):
    original_hash, original_id = employee_user.password_hash, employee_user.id
    assert api_client.login(employee_user.email, "test-password")[0] == 200
    password_session = Session.query.filter_by(user_id=original_id, revoked_at=None).order_by(Session.id.desc()).first()
    state = begin(api_client, entra)
    result = finish(api_client, state)
    assert result.status_code == 303 and result.location == "http://localhost:5173/reports?tab=manual"
    assert "HttpOnly" in " ".join(result.headers.getlist("Set-Cookie"))
    status, body = api_client.get("/api/sessions/whoami")
    assert status == 200 and body["data"]["user"]["id"] == original_id
    assert body["data"]["user"]["authMethod"] == "entra"
    assert body["data"]["user"]["passwordEnabled"] is True
    assert body["data"]["csrfToken"]
    db.session.refresh(password_session)
    assert password_session.revoked_at is not None
    assert employee_user.password_hash == original_hash
    assert api_client.post("/api/sessions/logout")[0] == 200
    assert api_client.login(employee_user.email, "test-password")[0] == 200


def test_microsoft_bypasses_only_password_onboarding(api_client, entra, employee_user):
    employee_user.first_login = True
    employee_user.temp_password_expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.session.commit()
    assert api_client.login(employee_user.email, "test-password")[0] == 403
    assert finish(api_client, begin(api_client, entra)).status_code == 303
    assert api_client.get("/api/sessions/whoami")[1]["data"]["user"]["firstLogin"] is False
    assert employee_user.first_login is True  # do not clear password onboarding globally


def test_callback_consumed_once(api_client, entra):
    state = begin(api_client, entra)
    assert finish(api_client, state).status_code == 303
    assert error(finish(api_client, state)) == "invalid_transaction"
    assert entra["exchanges"] == 1


def test_callback_bound_to_browser_and_state(api_client, entra, app):
    state = begin(api_client, entra)
    assert error(app.test_client().get("/auth/callback", query_string={"state": state, "code": "x"})) == "invalid_transaction"
    assert error(finish(api_client, "bad-state")) == "invalid_transaction"
    assert entra["exchanges"] == 0


def test_expired_transaction_denied(api_client, entra):
    state = begin(api_client, entra)
    AuthTransaction.query.update({"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)})
    db.session.commit()
    assert error(finish(api_client, state)) == "invalid_transaction"
    assert entra["exchanges"] == 0


def test_cancelled_and_duplicate_parameters(api_client, entra):
    state = begin(api_client, entra)
    assert error(api_client.client.get("/auth/callback", query_string={"state": state, "error": "access_denied", "error_description": "never-reflect"})) == "cancelled"
    state = begin(api_client, entra)
    assert error(api_client.client.get(f"/auth/callback?state={state}&state=other&code=x")) == "invalid_transaction"
    assert entra["exchanges"] == 0


@pytest.mark.parametrize("claim,value", [
    ("aud", "wrong-audience"), ("iss", "https://attacker.example"),
    ("tid", "33333333-3333-4333-8333-333333333333"), ("exp", 1), ("nbf", 9999999999), ("oid", "bad-id")])
def test_invalid_id_token_denied(api_client, entra, claim, value):
    entra["claims"][claim] = value
    assert error(finish(api_client, begin(api_client, entra))) == "invalid_response"


def test_unmapped_account_never_matches_by_email(api_client, entra, employee_user):
    entra["oid"] = str(uuid4())
    entra["claims"]["email"] = employee_user.email
    assert error(finish(api_client, begin(api_client, entra))) == "account_unavailable"


def test_msal_nonce_failure_denied(api_client, entra):
    entra["error"] = RuntimeError("nonce mismatch with sensitive provider details")
    assert error(finish(api_client, begin(api_client, entra))) == "invalid_response"


def test_deactivation_blocks_both_methods(api_client, entra, employee_user):
    assert finish(api_client, begin(api_client, entra)).status_code == 303
    employee_user.is_active = False
    db.session.commit()
    assert api_client.get("/api/sessions/whoami")[0] == 401
    assert api_client.login(employee_user.email, "test-password")[0] == 401
    assert error(finish(api_client, begin(api_client, entra))) == "account_unavailable"


def test_password_sessions_also_check_active_user(api_client, entra, employee_user):
    assert api_client.login(employee_user.email, "test-password")[0] == 200
    employee_user.is_active = False
    db.session.commit()
    assert api_client.get("/api/sessions/whoami")[0] == 401


def test_csrf_and_origin_protection(api_client, entra, employee_user):
    assert api_client.login(employee_user.email, "test-password")[0] == 200
    assert api_client.client.post("/api/sessions/logout").status_code == 403
    token = api_client.csrf_token
    response = api_client.client.post("/api/sessions/logout", headers={"X-CSRF-Token": token, "Origin": "https://attacker.example"})
    assert response.status_code == 403
    assert api_client.post("/api/sessions/logout")[0] == 200
    assert api_client.get("/api/sessions/whoami")[0] == 401


def test_no_password_hash_rejects_credentials_and_password_creation(api_client, entra, employee_user):
    original = employee_user.password_hash
    try:
        employee_user.password_hash = None
        db.session.commit()
        assert api_client.login(employee_user.email, "anything")[0] == 401
        assert finish(api_client, begin(api_client, entra)).status_code == 303
        assert api_client.get("/api/sessions/whoami")[1]["data"]["user"]["passwordEnabled"] is False
        assert api_client.post("/api/users/set-password", {"current_password": "x", "new_password": "new-password", "new_password_confirm": "new-password"})[0] == 401
    finally:
        employee_user.password_hash = original
        db.session.commit()


def test_admin_linking_duplicate_guest_restrictions_and_audit(api_client, entra, employee_user, manager_user):
    assert api_client.login("superadmin", "superadmin")[0] == 200
    path = f"/api/users/{employee_user.id}/entra-identity"
    code, body = api_client._write("put", path, {"object_id": str(uuid4()), "account_type": "member"})
    assert code == 200, body
    assert IdentityAudit.query.filter_by(user_id=employee_user.id, action="link").count() >= 2
    linked_oid = body["data"]["object_id"]
    assert api_client._write("put", f"/api/users/{manager_user.id}/entra-identity", {"object_id": linked_oid, "account_type": "member"})[0] == 400
    assert api_client._write("put", path, {"object_id": str(uuid4()), "account_type": "guest"})[0] == 400
    assert api_client.delete(path)[0] == 200
    assert not employee_user.entra_identity.active
    # Unlinking must not remove existing HPH password access.
    assert api_client.login(employee_user.email, "test-password")[0] == 200
    assert api_client.get(path)[0] == 403


def test_guest_can_only_use_assigned_client_role(api_client, entra, employee_user):
    original_role = employee_user.role
    client_type = RoleType.query.filter_by(code="client").one()
    role = Role(role_type_id=client_type.id, title="Entra test client")
    db.session.add(role)
    db.session.flush()
    try:
        employee_user.role = role
        link_identity(employee_user, entra["oid"], "guest", operator="pytest")
        db.session.commit()
        assert finish(api_client, begin(api_client, entra)).status_code == 303
        assert api_client.get("/api/sessions/whoami")[0] == 200
        employee_user.role = original_role
        db.session.commit()
        assert api_client.get("/api/sessions/whoami")[0] == 401
        assert api_client.login(employee_user.email, "test-password")[0] == 401
    finally:
        employee_user.role = original_role
        db.session.commit()
        db.session.delete(role)
        db.session.commit()


@pytest.mark.parametrize("value", ["https://attacker.example", "//attacker.example", "/%2fexample.com", "/\\\\evil", "/%0d%0aevil", "/auth/callback", "/api/users", "/login"])
def test_return_destination_allowlist(value):
    assert safe_return_to(value) == "/"


def test_bad_signature_denied(api_client, entra):
    entra["bad_signature"] = True
    assert error(finish(api_client, begin(api_client, entra))) == "invalid_response"


def test_expired_session_requires_new_login(api_client, entra, employee_user):
    assert finish(api_client, begin(api_client, entra)).status_code == 303
    Session.query.filter_by(user_id=employee_user.id, revoked_at=None).update(
        {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}, synchronize_session=False)
    db.session.commit()
    assert api_client.get("/api/sessions/whoami")[0] == 401


def test_unlink_revokes_microsoft_session(api_client, entra, employee_user):
    from app.entra.services import unlink_identity
    assert finish(api_client, begin(api_client, entra)).status_code == 303
    unlink_identity(employee_user, operator="pytest")
    db.session.commit()
    assert api_client.get("/api/sessions/whoami")[0] == 401
    assert api_client.login(employee_user.email, "test-password")[0] == 200


def test_no_password_provisioning_does_not_send_credential_email(api_client, entra, monkeypatch, manager_user):
    import app.users.routes as users_routes
    monkeypatch.setattr(users_routes, "send_temporary_password_email", lambda *a, **k: pytest.fail("Microsoft-only accounts must not get a password email"))
    assert api_client.login("superadmin", "superadmin")[0] == 200
    token = uuid4().hex[:8]
    role = Role.query.filter_by(role_type_id=RoleType.query.filter_by(code="client").one().id).first()
    temporary_role = None
    if role is None:
        temporary_role = role = Role(role_type_id=RoleType.query.filter_by(code="client").one().id, title="Provisioning Client")
        db.session.add(role)
        db.session.commit()
    user_id = None
    try:
        code, body = api_client.post("/api/users", {"email": f"entra-{token}@example.com", "first_name": "Guest", "last_name": "Test", "emp_id": f"ENTRA-{token}", "role_id": role.id, "project_id": manager_user.project_id, "password_login_enabled": False})
        assert code == 201, body
        user_id = body["data"]["id"]
        user = db.session.get(User, user_id)
        assert user.password_hash is None and not user.first_login
        assert body["data"]["password_enabled"] is False
        assert api_client.post(f"/api/users/{user_id}/resend-temporary-password")[0] == 403
    finally:
        if user_id:
            db.session.execute(db.delete(User).where(User.id == user_id))
        if temporary_role:
            db.session.delete(temporary_role)
        db.session.commit()


def test_operator_linking_is_audited_including_superadmin(app, entra, superadmin):
    result = app.test_cli_runner().invoke(args=["link-entra-user", "--user-id", str(superadmin.id),
        "--object-id", str(uuid4()), "--operator", "test-bootstrap"])
    assert result.exit_code == 0, result.output
    assert IdentityAudit.query.filter_by(user_id=superadmin.id, operator="test-bootstrap").one().action == "link"


def test_guest_role_cannot_be_changed_through_role_editor(api_client, entra, employee_user):
    original_role = employee_user.role
    guest_role = Role(role_type_id=RoleType.query.filter_by(code="client").one().id, title="Guest Role Change Test")
    db.session.add(guest_role)
    db.session.flush()
    try:
        employee_user.role = guest_role
        link_identity(employee_user, entra["oid"], "guest", operator="pytest")
        db.session.commit()
        assert api_client.login("superadmin", "superadmin")[0] == 200
        assert api_client.patch(f"/api/roles/{guest_role.id}", {"role_type_id": original_role.role_type_id})[0] == 422
    finally:
        employee_user.role = original_role
        db.session.commit()
        db.session.delete(guest_role)
        db.session.commit()


@pytest.mark.parametrize("wrong_nonce", [False, True])
def test_real_msal_code_flow_pkce_and_nonce(api_client, entra, monkeypatch, wrong_nonce):
    """Exercise the actual MSAL public flow API with HTTP-boundary fixtures."""
    import msal
    import requests
    import app.entra.routes as routes
    import app.entra.services as services
    from cryptography.hazmat.primitives.asymmetric import rsa
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(services, "signing_keys", lambda tenant: SimpleNamespace(
        get_signing_key_from_jwt=lambda token: SimpleNamespace(key=private.public_key())))
    wire = {}

    def response(payload):
        value = requests.Response()
        value.status_code = 200
        value._content = json.dumps(payload).encode()
        value.headers["Content-Type"] = "application/json"
        return value

    class MicrosoftHTTP:
        def get(self, url, **kwargs):
            assert url.endswith("/.well-known/openid-configuration"), url
            return response({"authorization_endpoint": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize",
                "token_endpoint": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token",
                "issuer": f"https://login.microsoftonline.com/{TENANT}/v2.0"})

        def post(self, url, data=None, **kwargs):
            assert data["code_verifier"]  # real MSAL supplies PKCE on code exchange
            assert "offline_access" not in data.get("scope", "")
            assert "graph" not in data.get("scope", "").lower()
            wire["exchanged"] = True
            now = int(datetime.now(timezone.utc).timestamp())
            claims = {"aud": CLIENT, "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
                "sub": "test-subject", "tid": TENANT, "oid": entra["oid"],
                "nonce": "wrong" if wrong_nonce else wire["nonce"], "iat": now, "nbf": now, "exp": now + 300}
            return response({"id_token": jwt.encode(claims, private, algorithm="RS256"), "token_type": "Bearer", "expires_in": 300})

    actual = msal.ConfidentialClientApplication(CLIENT, client_credential="fixture-secret",
        authority=f"https://login.microsoftonline.com/{TENANT}", http_client=MicrosoftHTTP(),
        instance_discovery=False, exclude_scopes=["offline_access"])
    monkeypatch.setattr(routes, "client", lambda: actual)
    start = api_client.client.get("/auth/login?return_to=/account")
    query = parse_qs(urlsplit(start.location).query)
    wire["nonce"] = query["nonce"][0]
    assert query["code_challenge_method"] == ["S256"]
    assert set(query["scope"][0].split()) == {"openid", "profile"}
    callback = finish(api_client, query["state"][0])
    assert wire["exchanged"]
    if wrong_nonce:
        assert error(callback) == "invalid_response"
    else:
        assert callback.location == "http://localhost:5173/account"
        assert api_client.get("/api/sessions/whoami")[1]["data"]["user"]["authMethod"] == "entra"
