"""Backend-for-frontend routes to the appraisal microservices.

``/api/appraisal/<path>`` goes to the Appraisal service and ``/api/form-builder/<path>`` to the
Form Builder. The browser keeps using its session cookie and encrypted payloads; the services
receive a 60-second token signed here with the user's identity and feature permissions.
"""

from dataclasses import dataclass

import requests
from flask import current_app, g, request

from app.extensions import db
from app.features.models import Feature
from app.responses import api_response
from app.roles.models import role_features
from appraisal_system.access_bff.gateway import bp
from appraisal_system.access_bff.service_auth import get_service_auth


FORWARDED_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


@dataclass(frozen=True)
class Upstream:
    name: str
    url_config_key: str
    audience: str
    # First path segments the browser may reach; health checks and API docs stay internal.
    public_prefixes: frozenset


UPSTREAMS = {
    "appraisal": Upstream(
        "Appraisal service",
        "APPRAISAL_SERVICE_URL",
        "hph-appraisal",
        frozenset({"cycles", "me", "reviews", "appraisals"}),
    ),
    "form-builder": Upstream(
        "Form Builder service",
        "FORM_BUILDER_SERVICE_URL",
        "hph-form-builder",
        frozenset({"forms", "form-templates", "form-versions"}),
    ),
}


def user_permissions(role):
    """Return the role's active feature grants as ``"<codename>:read"`` / ``":write"`` strings."""
    rows = db.session.execute(
        db.select(Feature.codename, role_features.c.can_read, role_features.c.can_write)
        .select_from(role_features.join(Feature, Feature.id == role_features.c.feature_id))
        .where(role_features.c.role_id == role.id, Feature.active.is_(True))
    ).all()

    permissions = []
    for row in rows:
        if row.can_read or row.can_write:
            permissions.append(f"{row.codename}:read")
        if row.can_write:
            permissions.append(f"{row.codename}:write")

    return permissions


def _forward(upstream_key, path):
    upstream = UPSTREAMS[upstream_key]
    user = getattr(g, "user", None)

    # load_session has already required a live session; a deactivated user must lose access at
    # once even though their session or Microsoft sign-in is still valid (FR-22).
    if user is None or not user.is_active:
        return api_response(message="Your account is not active.", status=401)

    segments = [segment for segment in path.split("/") if segment]
    if not segments or segments[0] not in upstream.public_prefixes or ".." in segments:
        return api_response(message="Not found.", status=404)

    base_url = current_app.config.get(upstream.url_config_key)
    service_auth = get_service_auth()
    if not base_url or service_auth.private_key is None:
        return api_response(message=f"The {upstream.name} is not configured.", status=503)

    token = service_auth.mint_user_token(
        upstream.audience,
        user.id,
        user.role.role_type.code,
        user.project_id,
        user_permissions(user.role),
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "caller_id": str(user.id),
        "Accept": "application/json",
    }

    body = request.get_data() or None
    if body is not None:
        headers["Content-Type"] = "application/json"

    try:
        response = requests.request(
            request.method,
            f"{base_url.rstrip('/')}/{'/'.join(segments)}",
            params=list(request.args.items(multi=True)),
            data=body,
            headers=headers,
            timeout=current_app.config["SERVICE_GATEWAY_TIMEOUT_SECONDS"],
        )
    except requests.RequestException as exc:
        current_app.logger.warning("%s unreachable: %s", upstream.name, exc)
        return api_response(message=f"The {upstream.name} is unavailable.", status=503)

    try:
        payload = response.json()
    except ValueError:
        payload = None

    if response.status_code < 400:
        return api_response(data=payload, message="", status=response.status_code)

    # Services answer errors as {data, message, success, code}; keep their message and code.
    payload = payload if isinstance(payload, dict) else {}
    return api_response(
        data=payload.get("data"),
        message=payload.get("message") or f"The {upstream.name} rejected the request.",
        status=response.status_code,
        code=payload.get("code"),
    )


@bp.route("/api/appraisal/<path:path>", methods=FORWARDED_METHODS)
def appraisal_proxy(path):
    return _forward("appraisal", path)


@bp.route("/api/form-builder/<path:path>", methods=FORWARDED_METHODS)
def form_builder_proxy(path):
    return _forward("form-builder", path)
