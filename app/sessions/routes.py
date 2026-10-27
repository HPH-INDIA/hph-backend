from datetime import datetime, timezone

from flask import after_this_request, g
from flask.views import MethodView
from flask_smorest import abort

from app.encryption.passwords import InvalidCredentialsError, verify_password
from app.extensions import db
from app.responses import MessageEnvelopeSchema
from app.sessions import bp
from app.sessions.hooks import SESSION_COOKIE_NAME
from app.sessions.models import Session
from app.sessions.schemas import LoginEnvelopeSchema, LoginSchema, SessionListEnvelopeSchema
from app.sessions.services import issue_session
from app.users.models import User


def _invalid_credentials():
    abort(401, message="Invalid email or password.")


@bp.route("/sessions/login")
class Login(MethodView):
    @bp.arguments(LoginSchema)
    @bp.response(200, LoginEnvelopeSchema)
    def post(self, payload):
        user = User.query.filter_by(email=payload["email"]).first()
        if user is None:
            _invalid_credentials()

        try:
            verify_password(user.password_hash, payload["password"])
        except InvalidCredentialsError:
            _invalid_credentials()

        if not user.is_active or not user.role.is_active:
            _invalid_credentials()

        if user.first_login and user.temp_password_expires_at is not None:
            if datetime.now(timezone.utc) > user.temp_password_expires_at:
                abort(
                    403,
                    message="Temporary password has expired.",
                    code="temp_password_expired",
                )

        from app.entra.services import guest_role_allowed
        if user.entra_identity and not guest_role_allowed(user.entra_identity):
            _invalid_credentials()
        issue_session(user)
        return {"status": 200, "message": "Login successful.", "data": {"user": user}}


@bp.route("/sessions/logout")
class Logout(MethodView):
    @bp.response(200, MessageEnvelopeSchema)
    def post(self):
        g.session.revoked_at = datetime.now(timezone.utc)
        db.session.commit()

        @after_this_request
        def _clear_session_cookie(response):
            response.delete_cookie(SESSION_COOKIE_NAME)
            return response

        return {"status": 200, "message": "Logged out successfully.", "data": None}


@bp.route("/sessions/whoami")
class WhoAmI(MethodView):
    @bp.response(200, LoginEnvelopeSchema)
    def get(self):
        # Same {"user": UserProfileSchema} shape as login/set-password, so the
        # frontend can restore auth state on a page refresh without knowing
        # its own user id in advance — load_session has already resolved
        # g.user from the session cookie by the time this runs.
        return {"status": 200, "message": "Current user retrieved successfully.", "data": {"user": g.user}}


@bp.route("/sessions/me")
class MySessions(MethodView):
    @bp.response(200, SessionListEnvelopeSchema)
    def get(self):
        now = datetime.now(timezone.utc)
        sessions = (
            Session.query.filter_by(user_id=g.user.id)
            .filter(Session.revoked_at.is_(None), Session.expires_at > now)
            .order_by(Session.created_at.desc())
            .all()
        )

        data = [
            {
                "id": s.id,
                "created_at": s.created_at,
                "last_seen_at": s.last_seen_at,
                "expires_at": s.expires_at,
                "ip_address": s.ip_address,
                "user_agent": s.user_agent,
                "is_current": s.id == g.session.id,
            }
            for s in sessions
        ]
        return {"status": 200, "message": "Sessions retrieved successfully.", "data": data}


@bp.route("/sessions/<int:session_id>")
class SessionDetail(MethodView):
    @bp.response(200, MessageEnvelopeSchema)
    def delete(self, session_id):
        # "log out other devices": revoke one of *your own* other sessions by
        # id. Deliberately separate from /sessions/logout (which revokes the
        # caller's current session and also clears their cookie) — this one
        # never touches the caller's own cookie, since it's meant for rows
        # other than the one marked isCurrent in GET /sessions/me.
        session = Session.query.get_or_404(session_id)
        if session.user_id != g.user.id:
            abort(403, message="You are not permitted to revoke this session.")

        session.revoked_at = datetime.now(timezone.utc)
        db.session.commit()
        return {"status": 200, "message": "Session revoked successfully.", "data": None}
