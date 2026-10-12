"""Shared service-to-service plumbing in common/: token signing and the HTTP client."""

import json
import time

import jwt
import pytest
import requests

import common.errors as errors
from common.service_auth import (
    ServiceAuth,
    generate_key_pair,
    load_private_key,
    parse_trusted_issuers,
)
from common.service_client import ServiceClient


@pytest.fixture()
def keys():
    return {"bff": generate_key_pair(), "appraisal": generate_key_pair()}


def _verifier(keys, audience="hph-appraisal"):
    trusted = parse_trusted_issuers(
        {
            "hph-bff": {"publicKey": keys["bff"][1], "tokenTypes": ["user"]},
            "hph-appraisal": {"publicKey": keys["appraisal"][1], "tokenTypes": ["service"]},
        }
    )
    return ServiceAuth("hph-appraisal", None, audience, trusted)


def _signer(keys, name, issuer):
    return ServiceAuth(issuer, load_private_key(keys[name][0]), None, {})


class TestServiceAuth:
    def test_user_token_round_trip(self, keys):
        token = _signer(keys, "bff", "hph-bff").mint_user_token(
            "hph-appraisal", 30, "employee", 1, ["appraisal_self:write"]
        )

        caller = _verifier(keys).verify(token)

        assert (caller.user_id, caller.role_type, caller.project_id) == (30, "employee", 1)
        assert caller.can("appraisal_self", "read") and caller.can("appraisal_self", "write")
        assert not caller.can("appraisal_review")

    def test_service_token_has_no_user_permissions(self, keys):
        token = _signer(keys, "appraisal", "hph-appraisal").mint_service_token("hph-appraisal")

        caller = _verifier(keys).verify(token)

        assert caller.is_service and caller.user_id == -1
        assert not caller.can("appraisal_cycle_admin")

    @pytest.mark.parametrize(
        "claims_override, message",
        [
            ({"aud": "hph-form-builder"}, "invalid token"),
            ({"exp": int(time.time()) - 120}, "invalid token"),
            ({"exp": int(time.time()) + 3600}, "lifetime is too long"),
            ({"typ": "service"}, "may not send service tokens"),
        ],
    )
    def test_rejects_bad_tokens(self, keys, claims_override, message):
        now = int(time.time())
        claims = {
            "iss": "hph-bff",
            "aud": "hph-appraisal",
            "sub": "user:30",
            "typ": "user",
            "iat": now,
            "exp": now + 60,
            "hph": {"uid": 30},
        }
        claims.update(claims_override)
        token = jwt.encode(claims, load_private_key(keys["bff"][0]), algorithm="EdDSA")

        with pytest.raises(errors.Unauthenticated, match=message):
            _verifier(keys).verify(token)

    def test_rejects_a_token_signed_with_another_key(self, keys):
        impostor_private, _ = generate_key_pair()
        token = ServiceAuth(
            "hph-bff", load_private_key(impostor_private), None, {}
        ).mint_user_token("hph-appraisal", 1, "admin", None, ["appraisal_cycle_admin:write"])

        with pytest.raises(errors.Unauthenticated, match="invalid token"):
            _verifier(keys).verify(token)

    def test_trusted_issuer_config_is_validated(self, keys):
        with pytest.raises(ValueError):
            parse_trusted_issuers(
                {"hph-bff": {"publicKey": keys["bff"][1], "tokenTypes": ["admin"]}}
            )


class FakeSession:
    def __init__(self, status=200, payload=None, error=None):
        self.status, self.payload, self.error = status, payload, error
        self.requests = []

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        self.requests.append({"method": method, "url": url, "headers": headers, "json": json})
        if self.error:
            raise self.error
        response = requests.models.Response()
        response.status_code = self.status
        response._content = json_dumps(self.payload).encode()
        return response


json_dumps = json.dumps


class TestServiceClient:
    def _client(self, keys, session, **kwargs):
        return ServiceClient(
            "http://forms.test/",
            "hph-form-builder",
            _signer(keys, "appraisal", "hph-appraisal"),
            session=session,
            **kwargs,
        )

    def test_sends_a_fresh_service_token(self, keys):
        session = FakeSession(payload={"ok": True})

        assert self._client(keys, session).get("/forms/resolve") == {"ok": True}

        sent = session.requests[0]
        assert sent["url"] == "http://forms.test/forms/resolve"
        assert sent["headers"]["caller_id"] == "-1"
        assert sent["headers"]["Authorization"].startswith("Bearer ")

    def test_unwraps_the_monolith_envelope(self, keys):
        session = FakeSession(payload={"status": 200, "message": "", "data": [1, 2]})

        assert self._client(keys, session, unwrap_envelope=True).get("/api/directory/users") == [
            1,
            2,
        ]

    @pytest.mark.parametrize(
        "session, expected",
        [
            (FakeSession(status=401, payload={"message": "bad token"}), errors.Unavailable),
            (FakeSession(status=403, payload={"message": "no"}), errors.Unavailable),
            (FakeSession(status=500, payload={}), errors.Unavailable),
            (FakeSession(error=requests.ConnectionError("down")), errors.Unavailable),
            (
                FakeSession(status=404, payload={"code": "NOT_FOUND", "message": "x"}),
                errors.UpstreamError,
            ),
        ],
    )
    def test_maps_failures(self, keys, session, expected):
        with pytest.raises(expected):
            self._client(keys, session).get("/forms/resolve")

    def test_upstream_errors_keep_status_and_code(self, keys):
        session = FakeSession(status=404, payload={"code": "NOT_FOUND", "message": "no form"})

        with pytest.raises(errors.UpstreamError) as raised:
            self._client(keys, session).get("/forms/resolve")

        assert (raised.value.status, raised.value.code, raised.value.message) == (
            404,
            "NOT_FOUND",
            "no form",
        )
