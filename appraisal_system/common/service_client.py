"""HTTP client for calls between HPH services, signed with a fresh service token per call."""

import requests

from . import errors
from .hph_logging import logging
from .service_auth import SERVICE_CALLER_ID


logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 10


class ServiceClient:
    """Calls another HPH service as this service.

    Parameters
    ----------
    base_url : str
        Root URL of the target service, without a trailing slash.
    audience : str
        Audience name the target service verifies (e.g. ``hph-form-builder``).
    service_auth : common.service_auth.ServiceAuth
        Signs the per-call service token.
    timeout : float, optional, default = DEFAULT_TIMEOUT_SECONDS
        Seconds before a call is abandoned.
    session : requests.Session, optional, default = None
        Session to send through; tests pass one that routes to an in-process app.
    unwrap_envelope : bool, optional, default = False
        Return ``body["data"]`` for services that answer with the monolith's
        ``{status, message, data}`` envelope.
    """

    def __init__(
        self,
        base_url,
        audience,
        service_auth,
        timeout=DEFAULT_TIMEOUT_SECONDS,
        session=None,
        unwrap_envelope=False,
    ):
        if not base_url:
            raise RuntimeError(f"no base URL configured for {audience}")

        self.base_url = base_url.rstrip("/")
        self.audience = audience
        self.service_auth = service_auth
        self.timeout = timeout
        self.session = session or requests.Session()
        self.unwrap_envelope = unwrap_envelope

    def get(self, path, params=None):
        return self.request("GET", path, params=params)

    def post(self, path, json_body=None):
        return self.request("POST", path, json_body=json_body)

    def put(self, path, json_body=None):
        return self.request("PUT", path, json_body=json_body)

    def request(self, method, path, params=None, json_body=None):
        """Send one request and return the decoded JSON body.

        Raises
        ------
        common.errors.Unavailable
            If the service cannot be reached, times out, answers 5xx, or refuses this service's
            token (401/403).
        common.errors.UpstreamError
            If the service answers another 4xx; carries the upstream status, code and message.
        """
        headers = {
            "Authorization": f"Bearer {self.service_auth.mint_service_token(self.audience)}",
            "caller_id": str(SERVICE_CALLER_ID),
            "Accept": "application/json",
        }

        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                params=params,
                json=json_body,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            logger.warning("%s %s%s failed: %s", method, self.audience, path, exc)
            raise errors.Unavailable(f"{self.audience} is unavailable") from exc

        body = _json_or_none(response)

        if response.status_code >= 500:
            logger.warning("%s %s%s answered %s", method, self.audience, path, response.status_code)
            raise errors.Unavailable(f"{self.audience} failed with status {response.status_code}")

        if response.status_code in (401, 403):
            # A refused service credential is a deployment problem (keys, trusted issuers), not
            # the end user's; passing the 401 on would look like their session expired.
            logger.error(
                "%s %s%s refused this service: %s %s",
                method,
                self.audience,
                path,
                response.status_code,
                (body or {}).get("message") if isinstance(body, dict) else body,
            )
            raise errors.Unavailable(f"{self.audience} refused this service's credentials")

        if response.status_code >= 400:
            body = body if isinstance(body, dict) else {}
            raise errors.UpstreamError(
                response.status_code,
                body.get("code"),
                body.get("message") or f"{self.audience} answered {response.status_code}",
                body.get("data"),
            )

        if self.unwrap_envelope and isinstance(body, dict):
            return body.get("data")

        return body


def _json_or_none(response):
    try:
        return response.json()
    except ValueError:
        return None
