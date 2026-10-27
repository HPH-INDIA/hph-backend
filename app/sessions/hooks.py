import hashlib
from datetime import datetime, timezone

from flask import g, request
from flask_smorest import abort

from app.extensions import db
from app.sessions.models import Session

SESSION_COOKIE_NAME = "session_token"

# Routes reachable without an existing session. Checked by exact (path, method).
EXEMPT_PATHS = {
    ("/api/sessions/login", "POST"),
    # §4b: must be reachable pre-login so a brand-new client can fetch the
    # shared key and encrypt its very first request (login itself) — see
    # app/encryption/hooks.py's ENCRYPTION_EXEMPT_PATHS for the other half
    # of this (it's also the one endpoint exempt from payload encryption).
    ("/api/encryption/current-key", "GET"),
}


def hash_session_token(raw_token):
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _unauthorized():
    abort(401, message="Authentication required.")


def load_session():
    """Runs after the (future) encryption before_request hook decrypts the
    body — see app/__init__.py's create_app for the registration order,
    which is load-bearing the same way it was for Django middleware in v1.
    """
    if not request.path.startswith("/api/"):
        return None
    if request.method == "OPTIONS":
        # CORS preflight — never carries the session cookie or any other
        # credentials, so there's nothing here for load_session to check.
        # flask-cors answers it directly; this just keeps it from being
        # rejected as unauthenticated first.
        return None
    if (request.path, request.method) in EXEMPT_PATHS:
        return None

    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw_token:
        return _unauthorized()

    session = Session.query.filter_by(session_key=hash_session_token(raw_token)).first()

    now = datetime.now(timezone.utc)
    if session is None or session.revoked_at is not None or session.expires_at <= now:
        return _unauthorized()

    user = session.user
    if user is None or not user.is_active or not user.role.is_active:
        return _unauthorized()
    from app.entra.services import guest_role_allowed
    identity = user.entra_identity
    if identity and not guest_role_allowed(identity):
        return _unauthorized()
    if session.auth_method == "entra":
        from flask import current_app
        if (not current_app.config["ENTRA_ENABLED"] or not identity or not identity.active
                or identity.tenant_id != current_app.config["ENTRA_TENANT_ID"]):
            return _unauthorized()
    session.last_seen_at = now
    db.session.commit()

    g.session = session
    g.user = session.user
    return None
