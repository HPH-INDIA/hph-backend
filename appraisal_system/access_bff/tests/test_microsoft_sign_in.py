"""Microsoft sign-in, driven through the real MSAL library against a fake Microsoft."""

import base64
import hashlib
import json
import secrets
import time
from datetime import timedelta
from urllib.parse import parse_qs, quote, urlsplit

import jwt
import pytest
import requests
from tests.conftest import ApiClient

from app.extensions import db
from appraisal_system.access_bff.entra.client import EntraClient
from appraisal_system.access_bff.entra.models import (
    EntraLoginTransaction,
    ExternalIdentity,
    IdentityStatus,
    LinkMethod,
)
from appraisal_system.access_bff.entra.services import utc_now, validate_settings


TENANT_ID = "11111111-2222-3333-4444-555555555555"
OTHER_TENANT_ID = "99999999-8888-7777-6666-555555555555"
CLIENT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
CLIENT_SECRET = "test-client-secret"
REDIRECT_URI = "http://localhost/auth/microsoft/callback"
FRONTEND = "http://frontend.test"
FIRST_ACCOUNT = "0b0b0b0b-0000-0000-0000-000000000001"
SECOND_ACCOUNT = "0b0b0b0b-0000-0000-0000-000000000002"


def _response(status, payload):
    response = requests.models.Response()
    response.status_code = status
    response._content = json.dumps(payload).encode()
    response.headers["Content-Type"] = "application/json"
    response.encoding = "utf-8"
    return response


def _pkce_challenge(verifier):
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


class FakeMicrosoft:
    """Microsoft's side: OpenID discovery, the sign-in page and the token endpoint."""

    def __init__(self):
        base = f"https://login.microsoftonline.com/{TENANT_ID}"
        self.issuer = f"{base}/v2.0"
        self.configuration = {
            "issuer": self.issuer,
            "authorization_endpoint": f"{base}/oauth2/v2.0/authorize",
            "token_endpoint": f"{base}/oauth2/v2.0/token",
            "jwks_uri": f"{base}/discovery/v2.0/keys",
        }
        self.codes = {}
        self.token_requests = []

    def get(self, url, params=None, headers=None, **kwargs):
        if url.endswith("/v2.0/.well-known/openid-configuration"):
            return _response(200, self.configuration)
        return _response(404, {"error": "not_found"})

    def post(self, url, params=None, data=None, headers=None, **kwargs):
        self.token_requests.append(dict(data or {}))
        entry = self.codes.pop((data or {}).get("code"), None)
        if entry is None:
            return _response(400, {"error": "invalid_grant", "error_description": "bad code"})
        if data.get("client_secret") != CLIENT_SECRET:
            return _response(401, {"error": "invalid_client"})
        if _pkce_challenge(data.get("code_verifier", "")) != entry["code_challenge"]:
            return _response(400, {"error": "invalid_grant", "error_description": "PKCE failed"})

        id_token = jwt.encode(entry["claims"], "fake-microsoft-signing-key", algorithm="HS256")
        return _response(
            200,
            {
                "token_type": "Bearer",
                "scope": "openid profile",
                "expires_in": 3600,
                "access_token": "unused",
                "id_token": id_token,
            },
        )

    def close(self):
        return None

    def sign_in(self, authorize_url, **claims):
        """The person signs in on Microsoft's page; returns the query Microsoft redirects with."""
        request = {
            key: values[0] for key, values in parse_qs(urlsplit(authorize_url).query).items()
        }
        now = int(time.time())
        id_claims = {
            "iss": self.issuer,
            "aud": CLIENT_ID,
            "iat": now,
            "nbf": now,
            "exp": now + 3600,
            "nonce": request["nonce"],
            "tid": TENANT_ID,
            "oid": FIRST_ACCOUNT,
            "name": "Test Person",
        }
        id_claims.update(claims)
        # Real ID tokens always carry a subject (pairwise per app); MSAL requires it.
        id_claims.setdefault("sub", f"subject-{id_claims['oid']}")
        code = secrets.token_urlsafe(16)
        self.codes[code] = {"claims": id_claims, "code_challenge": request["code_challenge"]}

        return {"code": code, "state": request["state"]}


@pytest.fixture
def microsoft(app, monkeypatch):
    settings = {
        "ENTRA_SIGN_IN_ENABLED": True,
        "ENTRA_TENANT_ID": TENANT_ID,
        "ENTRA_CLIENT_ID": CLIENT_ID,
        "ENTRA_CLIENT_SECRET": CLIENT_SECRET,
        "ENTRA_REDIRECT_URI": REDIRECT_URI,
        "ENTRA_LINK_BY_EMAIL": True,
        "PASSWORD_LOGIN_ENABLED": True,
        "FRONTEND_APP_URL": FRONTEND,
        "FRONTEND_LOGIN_URL": f"{FRONTEND}/login",
        "SESSION_COOKIE_SECURE": False,
    }
    for key, value in settings.items():
        monkeypatch.setitem(app.config, key, value)

    fake = FakeMicrosoft()
    client = EntraClient(TENANT_ID, CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, http_client=fake)
    monkeypatch.setitem(app.extensions, "entra_client", client)

    yield fake

    db.session.rollback()
    db.session.query(ExternalIdentity).delete()
    db.session.query(EntraLoginTransaction).delete()
    db.session.commit()


def _start(client, next_path=None):
    url = "/auth/microsoft/login"
    if next_path is not None:
        url += f"?next={quote(next_path, safe='')}"
    response = client.get(url)
    assert response.status_code == 302, response.data
    return response


def _sign_in(client, microsoft, next_path=None, **claims):
    start = _start(client, next_path)
    callback_query = microsoft.sign_in(start.headers["Location"], **claims)
    return client.get("/auth/microsoft/callback", query_string=callback_query)


def _sso_error(response):
    location = urlsplit(response.headers["Location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == f"{FRONTEND}/login"
    return parse_qs(location.query)["sso_error"][0]


def _session_cookie(response):
    return [
        header for header in response.headers.getlist("Set-Cookie") if "session_token=" in header
    ]


class TestSwitch:
    def test_off_by_default(self, app):
        assert app.config["ENTRA_SIGN_IN_ENABLED"] is False
        assert app.test_client().get("/auth/microsoft/login").status_code == 404
        assert app.test_client().get("/auth/microsoft/callback").status_code == 404

    def test_password_sign_in_can_be_switched_off(
        self, app, api_client, employee_user, monkeypatch
    ):
        monkeypatch.setitem(app.config, "PASSWORD_LOGIN_ENABLED", False)

        status, body = api_client.login(employee_user.email, "test-password")

        assert status == 403
        assert body["code"] == "password_login_disabled"

    def test_misconfiguration_fails_at_start_up(self):
        with pytest.raises(RuntimeError) as raised:
            validate_settings(
                {
                    "ENTRA_TENANT_ID": "common",
                    "ENTRA_CLIENT_ID": CLIENT_ID,
                    "ENTRA_REDIRECT_URI": "http://hph.example/auth/microsoft/callback",
                }
            )

        message = str(raised.value)
        assert "ENTRA_TENANT_ID" in message
        assert "ENTRA_CLIENT_SECRET" in message
        assert "https" in message

    def test_valid_settings_pass(self):
        validate_settings(
            {
                "ENTRA_TENANT_ID": TENANT_ID,
                "ENTRA_CLIENT_ID": CLIENT_ID,
                "ENTRA_CLIENT_SECRET": CLIENT_SECRET,
                "ENTRA_REDIRECT_URI": "https://hph.example/auth/microsoft/callback",
            }
        )


class TestStart:
    def test_redirects_to_microsoft_for_hph_tenant_with_pkce(self, app, microsoft):
        response = _start(app.test_client(), next_path="/appraisals")

        location = urlsplit(response.headers["Location"])
        query = {key: values[0] for key, values in parse_qs(location.query).items()}
        assert response.headers["Location"].startswith(
            f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/authorize"
        )
        assert query["client_id"] == CLIENT_ID
        assert query["redirect_uri"] == REDIRECT_URI
        assert query["code_challenge_method"] == "S256"
        assert query["prompt"] == "select_account"
        assert "offline_access" not in query["scope"]
        cookie = response.headers["Set-Cookie"]
        assert cookie.startswith("entra_sign_in=")
        assert "HttpOnly" in cookie and "Path=/auth/microsoft" in cookie

    def test_microsoft_unreachable(self, app, microsoft, monkeypatch):
        def unreachable(*args, **kwargs):
            raise requests.ConnectionError("no route")

        monkeypatch.setattr(microsoft, "get", unreachable)

        response = app.test_client().get("/auth/microsoft/login")

        assert _sso_error(response) == "microsoft_unavailable"


class TestFirstSignIn:
    def test_links_by_email_and_starts_an_hph_session(self, app, microsoft, employee_user):
        client = app.test_client()

        response = _sign_in(
            client, microsoft, next_path="/appraisals/7", preferred_username=employee_user.email
        )

        assert response.status_code == 302
        assert response.headers["Location"] == f"{FRONTEND}/appraisals/7"
        assert _session_cookie(response)
        identity = ExternalIdentity.query.filter_by(user_id=employee_user.id).one()
        assert (identity.object_id, identity.link_method) == (FIRST_ACCOUNT, LinkMethod.EMAIL_MATCH)
        # The PKCE verifier was sent and no refresh token was asked for.
        assert microsoft.token_requests[0]["code_verifier"]
        assert "offline_access" not in microsoft.token_requests[0].get("scope", "")

        status, body = ApiClient(client).get("/api/sessions/whoami")
        assert status == 200
        assert body["data"]["user"]["email"] == employee_user.email

    def test_later_sign_ins_use_the_microsoft_id_not_the_email(self, app, microsoft, employee_user):
        _sign_in(app.test_client(), microsoft, preferred_username=employee_user.email)

        response = _sign_in(
            app.test_client(), microsoft, preferred_username="renamed.person@example.com"
        )

        assert response.headers["Location"] == f"{FRONTEND}/"
        assert _session_cookie(response)

    @pytest.mark.parametrize(
        "claims, reason",
        [
            ({"tid": OTHER_TENANT_ID}, "wrong_tenant"),
            ({"preferred_username": "visitor_gmail.com#EXT#@hph.onmicrosoft.com"}, "guest_account"),
            ({"preferred_username": "nobody@example.com"}, "not_linked"),
            ({"preferred_username": "superadmin"}, "admin_link_required"),
        ],
    )
    def test_refusals(self, app, microsoft, claims, reason):
        response = _sign_in(app.test_client(), microsoft, **claims)

        assert _sso_error(response) == reason
        assert not _session_cookie(response)
        assert ExternalIdentity.query.count() == 0

    def test_inactive_users_are_refused(self, app, microsoft, employee_user):
        employee_user.is_active = False
        db.session.commit()
        try:
            response = _sign_in(
                app.test_client(), microsoft, preferred_username=employee_user.email
            )
        finally:
            employee_user.is_active = True
            db.session.commit()

        assert _sso_error(response) == "inactive"

    def test_a_second_microsoft_account_is_not_linked_silently(self, app, microsoft, employee_user):
        _sign_in(app.test_client(), microsoft, preferred_username=employee_user.email)

        response = _sign_in(
            app.test_client(), microsoft, oid=SECOND_ACCOUNT, preferred_username=employee_user.email
        )

        assert _sso_error(response) == "linked_to_another_account"


class TestCallbackSafety:
    def test_callback_is_single_use(self, app, microsoft, employee_user):
        client = app.test_client()
        start = _start(client)
        cookie_value = client.get_cookie("entra_sign_in", path="/auth/microsoft").value
        callback_query = microsoft.sign_in(
            start.headers["Location"], preferred_username=employee_user.email
        )
        assert _session_cookie(client.get("/auth/microsoft/callback", query_string=callback_query))

        client.set_cookie("entra_sign_in", cookie_value, path="/auth/microsoft")
        replay = client.get("/auth/microsoft/callback", query_string=callback_query)

        assert _sso_error(replay) == "expired"

    def test_missing_cookie(self, app, microsoft, employee_user):
        start = _start(app.test_client())
        callback_query = microsoft.sign_in(
            start.headers["Location"], preferred_username=employee_user.email
        )

        response = app.test_client().get("/auth/microsoft/callback", query_string=callback_query)

        assert _sso_error(response) == "expired"

    def test_expired_sign_in(self, app, microsoft, employee_user):
        client = app.test_client()
        start = _start(client)
        db.session.query(EntraLoginTransaction).update(
            {"expires_at": utc_now() - timedelta(minutes=1)}
        )
        db.session.commit()

        callback_query = microsoft.sign_in(
            start.headers["Location"], preferred_username=employee_user.email
        )
        response = client.get("/auth/microsoft/callback", query_string=callback_query)

        assert _sso_error(response) == "expired"

    def test_state_and_nonce_are_checked(self, app, microsoft, employee_user):
        client = app.test_client()
        start = _start(client)
        callback_query = microsoft.sign_in(
            start.headers["Location"], preferred_username=employee_user.email
        )
        callback_query["state"] = "forged"
        assert _sso_error(client.get("/auth/microsoft/callback", query_string=callback_query)) == (
            "invalid_response"
        )

        response = _sign_in(
            app.test_client(), microsoft, preferred_username=employee_user.email, nonce="forged"
        )
        assert _sso_error(response) == "invalid_response"

    def test_cancelled_at_microsoft(self, app, microsoft):
        client = app.test_client()
        _start(client)

        response = client.get(
            "/auth/microsoft/callback",
            query_string={"error": "access_denied", "error_description": "user cancelled"},
        )

        assert _sso_error(response) == "cancelled"

    @pytest.mark.parametrize("next_path", ["https://evil.example/x", "//evil.example", "/\\evil"])
    def test_next_path_cannot_leave_hph(self, app, microsoft, employee_user, next_path):
        response = _sign_in(
            app.test_client(),
            microsoft,
            next_path=next_path,
            preferred_username=employee_user.email,
        )

        assert response.headers["Location"] == f"{FRONTEND}/"


class TestAdminCommands:
    def test_admins_are_linked_by_command(self, app, microsoft):
        runner = app.test_cli_runner()

        result = runner.invoke(
            args=["entra-link", "--email", "superadmin", "--object-id", SECOND_ACCOUNT]
        )
        response = _sign_in(app.test_client(), microsoft, oid=SECOND_ACCOUNT)

        assert result.exit_code == 0, result.output
        assert _session_cookie(response)

    def test_unlink_blocks_microsoft_sign_in(self, app, microsoft, employee_user):
        runner = app.test_cli_runner()
        runner.invoke(
            args=["entra-link", "--email", employee_user.email, "--object-id", FIRST_ACCOUNT]
        )

        result = runner.invoke(args=["entra-unlink", "--email", employee_user.email])
        response = _sign_in(app.test_client(), microsoft, preferred_username=employee_user.email)

        assert result.exit_code == 0, result.output
        assert ExternalIdentity.query.one().status == IdentityStatus.REVOKED
        assert _sso_error(response) == "not_linked"

    def test_relinking_needs_replace(self, app, microsoft, employee_user):
        runner = app.test_cli_runner()
        runner.invoke(
            args=["entra-link", "--email", employee_user.email, "--object-id", FIRST_ACCOUNT]
        )

        refused = runner.invoke(
            args=["entra-link", "--email", employee_user.email, "--object-id", SECOND_ACCOUNT]
        )
        replaced = runner.invoke(
            args=[
                "entra-link",
                "--email",
                employee_user.email,
                "--object-id",
                SECOND_ACCOUNT,
                "--replace",
            ]
        )

        assert refused.exit_code != 0 and "--replace" in refused.output
        assert replaced.exit_code == 0, replaced.output
        assert ExternalIdentity.query.filter_by(status=IdentityStatus.ACTIVE).one().object_id == (
            SECOND_ACCOUNT
        )
