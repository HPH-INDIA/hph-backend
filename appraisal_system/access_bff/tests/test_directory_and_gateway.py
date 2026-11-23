"""The Access/BFF plugin inside the monolith: the directory API and the gateway."""

import json
from types import SimpleNamespace

import jwt
import pytest
import requests

from app.extensions import db
from appraisal_system.common.service_auth import (
    ServiceAuth,
    generate_key_pair,
    load_private_key,
    load_public_key,
    parse_trusted_issuers,
)


@pytest.fixture
def service_keys(app, monkeypatch):
    """Give the app test keys: it signs as hph-bff and trusts two services' tokens."""
    bff_private, bff_public = generate_key_pair()
    appraisal_private, appraisal_public = generate_key_pair()
    other_private, other_public = generate_key_pair()

    trusted = parse_trusted_issuers(
        {
            "hph-appraisal": {"publicKey": appraisal_public, "tokenTypes": ["service"]},
            # Trusted to sign tokens but not allowed to read the directory.
            "hph-other": {"publicKey": other_public, "tokenTypes": ["service"]},
        }
    )
    monkeypatch.setitem(
        app.extensions,
        "service_auth",
        ServiceAuth("hph-bff", load_private_key(bff_private), "hph-access", trusted),
    )

    return SimpleNamespace(
        bff_public=bff_public,
        appraisal=ServiceAuth("hph-appraisal", load_private_key(appraisal_private), None, {}),
        other=ServiceAuth("hph-other", load_private_key(other_private), None, {}),
    )


def _service_headers(signer, caller_id="-1"):
    return {
        "Authorization": f"Bearer {signer.mint_service_token('hph-access')}",
        "caller_id": caller_id,
    }


class TestDirectory:
    def test_requires_a_service_token(self, app, service_keys):
        response = app.test_client().get("/api/directory/users")

        assert response.status_code == 401
        # Plain JSON: service calls skip the browser payload encryption.
        assert "X-Encryption-Key-Version" not in response.headers
        assert response.json["message"] == "missing bearer token"

    def test_browser_session_is_not_enough(self, app, api_client, employee_user, service_keys):
        api_client.login(employee_user.email, "test-password")

        assert api_client.client.get("/api/directory/users").status_code == 401

    def test_lists_users_with_reporting_lines(self, app, service_keys, lead_user, employee_user):
        response = app.test_client().get(
            f"/api/directory/users?ids={employee_user.id},{lead_user.id}",
            headers=_service_headers(service_keys.appraisal),
        )

        assert response.status_code == 200
        users = {item["id"]: item for item in response.json["data"]}
        assert users[employee_user.id]["roleTypeCode"] == "employee"
        assert users[employee_user.id]["reportsToId"] == lead_user.id
        assert users[lead_user.id]["roleTypeCode"] == "lead"
        assert set(users[employee_user.id]) == {
            "id",
            "empId",
            "firstName",
            "lastName",
            "email",
            "roleTypeCode",
            "roleTitle",
            "projectId",
            "reportsToId",
            "isActive",
            "lastWorkingDay",
        }

    def test_filters_combine(self, app, service_keys, lead_user, employee_user, manager_user):
        response = app.test_client().get(
            f"/api/directory/users?projectId={employee_user.project_id}&roleTypes=employee,lead"
            "&active=true",
            headers=_service_headers(service_keys.appraisal),
        )

        role_types = {item["roleTypeCode"] for item in response.json["data"]}
        ids = {item["id"] for item in response.json["data"]}
        assert role_types <= {"employee", "lead"}
        assert {employee_user.id, lead_user.id} <= ids
        assert manager_user.id not in ids

    def test_projects(self, app, service_keys):
        response = app.test_client().get(
            "/api/directory/projects", headers=_service_headers(service_keys.appraisal)
        )

        assert "CODING" in {item["name"] for item in response.json["data"]}

    def test_untrusted_or_unlisted_services_are_refused(self, app, service_keys):
        stranger_private, _ = generate_key_pair()
        stranger = ServiceAuth("hph-stranger", load_private_key(stranger_private), None, {})
        client = app.test_client()

        assert (
            client.get("/api/directory/users", headers=_service_headers(stranger)).status_code
            == 401
        )
        assert (
            client.get(
                "/api/directory/users", headers=_service_headers(service_keys.other)
            ).status_code
            == 403
        )

    def test_caller_id_must_mark_a_service(self, app, service_keys):
        response = app.test_client().get(
            "/api/directory/users", headers=_service_headers(service_keys.appraisal, caller_id="5")
        )

        assert response.status_code == 401

    def test_dropped_caller_id_header_is_tolerated(self, app, service_keys):
        # Werkzeug 3 and gunicorn >= 22 strip headers containing "_" before they reach the app.
        headers = _service_headers(service_keys.appraisal)
        del headers["caller_id"]

        response = app.test_client().get("/api/directory/projects", headers=headers)

        assert response.status_code == 200


class FakeUpstream:
    """Replaces requests.request in the gateway and records what it was asked to send."""

    def __init__(self, status=200, payload=None, error=None):
        self.calls = []
        self.status = status
        self.payload = payload if payload is not None else {"ok": True}
        self.error = error

    def __call__(self, method, url, params=None, data=None, headers=None, timeout=None):
        self.calls.append(
            {"method": method, "url": url, "params": params, "data": data, "headers": headers}
        )
        if self.error:
            raise self.error
        response = requests.models.Response()
        response.status_code = self.status
        response._content = json.dumps(self.payload).encode()
        response.headers["Content-Type"] = "application/json"
        return response


@pytest.fixture
def upstream(app, monkeypatch, service_keys):
    fake = FakeUpstream()
    monkeypatch.setattr("appraisal_system.access_bff.gateway.routes.requests.request", fake)
    monkeypatch.setitem(app.config, "APPRAISAL_SERVICE_URL", "http://appraisal.test")
    monkeypatch.setitem(app.config, "FORM_BUILDER_SERVICE_URL", "http://forms.test/")
    return fake


class TestGateway:
    def test_requires_a_session(self, api_client, upstream):
        status, body = api_client.get("/api/appraisal/me/appraisals")

        assert status == 401
        assert upstream.calls == []

    def test_forwards_with_a_signed_user_token(
        self, api_client, employee_user, upstream, service_keys
    ):
        upstream.payload = [{"id": 7, "status": "draft"}]
        api_client.login(employee_user.email, "test-password")

        status, body = api_client.get("/api/appraisal/me/appraisals?cycle_id=3")

        assert status == 200
        assert body["data"] == [{"id": 7, "status": "draft"}]
        call = upstream.calls[0]
        assert call["method"] == "GET"
        assert call["url"] == "http://appraisal.test/me/appraisals"
        assert call["params"] == [("cycle_id", "3")]
        assert call["headers"]["caller_id"] == str(employee_user.id)

        token = call["headers"]["Authorization"].removeprefix("Bearer ")
        claims = jwt.decode(
            token,
            load_public_key(service_keys.bff_public),
            algorithms=["EdDSA"],
            audience="hph-appraisal",
        )
        assert claims["iss"] == "hph-bff"
        assert claims["hph"]["uid"] == employee_user.id
        assert claims["hph"]["rt"] == "employee"
        # Granted to the Employee starter role by the seed-appraisal-features migration.
        assert {"appraisal_self:read", "appraisal_self:write"} <= set(claims["hph"]["perm"])
        assert claims["exp"] - claims["iat"] == 60

    def test_forwards_decrypted_bodies_and_routes_to_the_form_builder(
        self, api_client, superadmin, upstream
    ):
        api_client.login("superadmin", "superadmin")
        upstream.status = 201

        status, body = api_client.post("/api/form-builder/forms", {"name": "Coding"})

        call = upstream.calls[0]
        assert status == 201
        assert call["url"] == "http://forms.test/forms"
        assert json.loads(call["data"]) == {"name": "Coding"}
        assert call["headers"]["Content-Type"] == "application/json"

    def test_service_errors_keep_their_message_code_and_detail(
        self, api_client, employee_user, upstream
    ):
        upstream.status = 400
        upstream.payload = {
            "data": {"errors": [{"path": "self_rating", "message": "is required"}]},
            "message": "complete the required fields",
            "success": False,
            "code": "INVALID_ARGUMENT",
        }
        api_client.login(employee_user.email, "test-password")

        status, body = api_client.post("/api/appraisal/appraisals/7/submit")

        assert status == 400
        assert body["message"] == "complete the required fields"
        assert body["code"] == "INVALID_ARGUMENT"
        assert body["data"]["errors"][0]["path"] == "self_rating"

    @pytest.mark.parametrize(
        "path", ["/api/appraisal/healthz", "/api/appraisal/ui/", "/api/appraisal/cycles/../healthz"]
    )
    def test_only_public_paths_are_forwarded(self, api_client, employee_user, upstream, path):
        api_client.login(employee_user.email, "test-password")

        status, _ = api_client.get(path)

        assert status == 404
        assert upstream.calls == []

    def test_unreachable_service(self, api_client, employee_user, upstream):
        upstream.error = requests.ConnectionError("refused")
        api_client.login(employee_user.email, "test-password")

        status, body = api_client.get("/api/appraisal/me/appraisals")

        assert status == 503
        assert body["message"] == "The Appraisal service is unavailable."

    def test_unconfigured_service(self, api_client, employee_user, upstream, app, monkeypatch):
        monkeypatch.setitem(app.config, "APPRAISAL_SERVICE_URL", "")
        api_client.login(employee_user.email, "test-password")

        status, _ = api_client.get("/api/appraisal/me/appraisals")

        assert status == 503
        assert upstream.calls == []

    def test_deactivated_user_loses_access_immediately(self, api_client, employee_user, upstream):
        api_client.login(employee_user.email, "test-password")
        employee_user.is_active = False
        db.session.commit()
        try:
            status, _ = api_client.get("/api/appraisal/me/appraisals")
        finally:
            employee_user.is_active = True
            db.session.commit()

        assert status == 401
        assert upstream.calls == []
