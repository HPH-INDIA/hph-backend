"""Connexion wiring shared by the HPH microservices.

It authenticates every API
request, exposes the caller to handlers, and renders errors as
``{"data", "message", "success": false, "code"}``.
"""

import os

import connexion
from connexion.exceptions import ProblemException
from flask import current_app, g, jsonify, request

from . import errors
from .configdb import get_sql_alchemy, set_sql_alchemy
from .hph_logging import logging
from .service_auth import SERVICE_CALLER_ID, ServiceAuth, bearer_token


logger = logging.getLogger(__name__)

CALLER_ID_HEADER = "caller_id"
PUBLIC_PATH_PREFIXES = ("/healthz", "/ui", "/openapi.json", "/openapi.yaml")


def env_flag(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default

    return value.strip().lower() in ("1", "true", "yes", "on")


def create_connexion_app(import_name, specification_dir, audience, database_url, service_auth=None):
    """Build a Connexion app with the database, authentication and error handling wired in.

    The caller adds the API spec with ``connex_app.add_api(...)`` after importing its models,
    so operationIds can resolve to handlers that use the models.

    Parameters
    ----------
    import_name : str
        Module name passed to ``connexion.App``.
    specification_dir : str
        Directory holding the service's OpenAPI YAML.
    audience : str
        Audience name this service accepts tokens for (e.g. ``hph-appraisal``).
    database_url : str
        SQLAlchemy database URL.
    service_auth : common.service_auth.ServiceAuth, optional, default = None
        Token signer/verifier; built from ``SERVICE_AUTH_*`` environment variables when omitted.

    Returns
    -------
    connexion.App
    """
    swagger_ui = env_flag("SWAGGER_UI_ENABLED")
    connex_app = connexion.App(
        import_name,
        specification_dir=specification_dir,
        options={"swagger_ui": swagger_ui, "serve_spec": swagger_ui},
    )
    app = connex_app.app

    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    if not database_url.startswith("sqlite"):
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_size": 5, "max_overflow": 5}
    set_sql_alchemy(app)

    app.extensions["service_auth"] = service_auth or ServiceAuth.from_env(audience=audience)

    app.before_request(avoid_empty_body_json_decode)
    app.before_request(authenticate_request)

    connex_app.add_error_handler(errors.HTTPException, render_http_exception)
    connex_app.add_error_handler(ProblemException, render_problem)
    connex_app.add_error_handler(Exception, render_unexpected_exception)

    app.add_url_rule("/healthz", "healthz", lambda: jsonify({"status": "ok"}))

    return connex_app


def avoid_empty_body_json_decode():
    """Drop ``Content-Type`` on empty bodies so Connexion 2 does not try to decode them as JSON."""
    if request.environ.get("CONTENT_LENGTH") in (None, "", "0"):
        request.environ.pop("CONTENT_TYPE", None)


def authenticate_request():
    """Verify the bearer token and the ``caller_id`` header, then store the caller on ``g``."""
    if request.method == "OPTIONS" or request.path.startswith(PUBLIC_PATH_PREFIXES):
        return None

    service_auth = current_app.extensions["service_auth"]
    caller = service_auth.verify(bearer_token(request.headers.get("Authorization")))

    raw_caller_id = request.headers.get(CALLER_ID_HEADER)
    try:
        caller_id = int(raw_caller_id)
    except (TypeError, ValueError) as exc:
        raise errors.Unauthenticated(f"{CALLER_ID_HEADER} header must be an integer") from exc

    expected = SERVICE_CALLER_ID if caller.is_service else caller.user_id
    if caller_id != expected:
        raise errors.Unauthenticated(f"{CALLER_ID_HEADER} does not match the bearer token")

    g.caller = caller

    return None


def current_caller():
    """Return the verified caller of the current request.

    Raises
    ------
    common.errors.Unauthenticated
        Outside an authenticated request.
    """
    caller = getattr(g, "caller", None)
    if caller is None:
        raise errors.Unauthenticated("request is not authenticated")

    return caller


def _rollback():
    try:
        get_sql_alchemy().session.rollback()
    except Exception:  # noqa: BLE001 // a failed rollback must not mask the original error
        logger.exception("session rollback failed")


def render_http_exception(exc):
    _rollback()
    if exc.status >= 500:
        logger.error("%s %s failed: %s", request.method, request.path, exc.message)
    else:
        logger.info("%s %s rejected (%s): %s", request.method, request.path, exc.code, exc.message)

    response = jsonify(exc.to_body())
    response.status_code = exc.status

    return response


def render_problem(exc):
    """Render Connexion's request-validation problems in the same error shape as service errors."""
    _rollback()
    code = "INVALID_ARGUMENT" if exc.status == 400 else errors.HTTPException.code
    if exc.status == 401:
        code = errors.Unauthenticated.code
    elif exc.status == 403:
        code = errors.PermissionDenied.code
    elif exc.status == 404:
        code = errors.NotFound.code

    response = jsonify(
        {"data": None, "message": exc.detail or exc.title, "success": False, "code": code}
    )
    response.status_code = exc.status

    return response


def render_unexpected_exception(exc):
    # Werkzeug HTTP errors (404 route, 405 method) keep their own status.
    status = getattr(exc, "code", None)
    if isinstance(status, int) and 400 <= status < 500:
        _rollback()
        response = jsonify(
            {"data": None, "message": str(exc), "success": False, "code": "INVALID_ARGUMENT"}
        )
        response.status_code = status
        return response

    _rollback()
    logger.exception("%s %s raised an unexpected error", request.method, request.path)
    response = jsonify(errors.Internal("internal server error").to_body())
    response.status_code = 500

    return response
