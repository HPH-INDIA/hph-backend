"""Token checks for calls between this app (Access/BFF) and the HPH microservices.

Tokens are signed with the keys in the ``SERVICE_AUTH_*`` environment variables; see
``common/service_auth.py``.
"""

from flask import current_app, request
from flask_smorest import abort

from appraisal_system.common import errors
from appraisal_system.common.service_auth import SERVICE_CALLER_ID, ServiceAuth, bearer_token


ACCESS_AUDIENCE = "hph-access"
# Services allowed to read the directory.
DIRECTORY_CLIENT_ISSUERS = ("hph-appraisal", "hph-form-builder")


def init_service_auth(app):
    app.extensions["service_auth"] = ServiceAuth.from_env(audience=ACCESS_AUDIENCE)


def get_service_auth():
    return current_app.extensions["service_auth"]


def require_service_caller(allowed_issuers=DIRECTORY_CLIENT_ISSUERS):
    """Abort unless the request carries a valid service token from an allowed service.

    Returns
    -------
    common.service_auth.Caller
    """
    try:
        caller = get_service_auth().verify(bearer_token(request.headers.get("Authorization")))
    except errors.Unauthenticated as exc:
        abort(401, message=exc.message)

    if not caller.is_service:
        abort(403, message="This endpoint is only for HPH services.")
    if caller.issuer not in allowed_issuers:
        abort(403, message=f"{caller.issuer} may not call this endpoint.")
    # Werkzeug 3 and gunicorn >= 22 drop headers containing "_", so caller_id may not arrive; the
    # signed token already identifies the service, and a caller_id that does arrive must agree.
    caller_id = request.headers.get("caller_id")
    if caller_id is not None and caller_id != str(SERVICE_CALLER_ID):
        abort(401, message="caller_id must be -1 for service calls.")

    return caller
