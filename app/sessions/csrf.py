import hashlib
import hmac

from flask import current_app, g, request
from flask_smorest import abort


def csrf_token(session):
    return hmac.new(current_app.config["SECRET_KEY"].encode(),
        ("hph-csrf:" + session.session_key).encode(), hashlib.sha256).hexdigest()


def protect_mutation():
    if not current_app.config["CSRF_ENABLED"] or request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    if not request.path.startswith("/api/"):
        return
    allowed = {current_app.config["PUBLIC_APP_ORIGIN"], *current_app.config["CORS_ALLOWED_ORIGINS"]}
    origin = request.headers.get("Origin")
    if origin and origin not in allowed:
        abort(403, message="Request origin is not allowed.", code="csrf_failed")
    if request.path == "/api/sessions/login":
        if not origin:
            abort(403, message="An allowed Origin header is required.", code="csrf_failed")
        return
    session = getattr(g, "session", None)
    supplied = request.headers.get("X-CSRF-Token", "")
    if session is not None and not hmac.compare_digest(supplied, csrf_token(session)):
        abort(403, message="Session protection token is missing or stale. Refresh and try again.", code="csrf_failed")


def session_response_headers(response):
    if request.path.startswith("/api/sessions/") or request.path == "/api/users/set-password":
        response.headers["Cache-Control"] = "no-store"
    return response
