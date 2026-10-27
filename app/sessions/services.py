import secrets
from datetime import datetime, timedelta, timezone

from flask import after_this_request, current_app, g, request

from app.extensions import db
from app.sessions.hooks import SESSION_COOKIE_NAME, hash_session_token
from app.sessions.models import Session


def issue_session(user, auth_method="password"):
    """Creates a new Session row for `user` and sets the session cookie on
    the response Flask is about to build. Shared by the login route and the
    set-password route — §3 requires both to issue a fresh session rather
    than trusting one created before a password reset.
    """
    # Replace the current browser session, including sessions created before login.
    old_token = request.cookies.get(SESSION_COOKIE_NAME)
    if old_token:
        Session.query.filter_by(session_key=hash_session_token(old_token), revoked_at=None).update(
            {"revoked_at": datetime.now(timezone.utc)}, synchronize_session=False)
    timeout_minutes = user.role.role_type.session_timeout_minutes
    raw_token = secrets.token_urlsafe(32)

    session = Session(
        user_id=user.id,
        auth_method=auth_method,
        session_key=hash_session_token(raw_token),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=timeout_minutes),
        ip_address=request.remote_addr,
        user_agent=request.headers.get("User-Agent"),
    )
    db.session.add(session)
    db.session.commit()
    g.session, g.user = session, user

    @after_this_request
    def _set_session_cookie(response):
        response.set_cookie(
            SESSION_COOKIE_NAME,
            raw_token,
            httponly=True,
            secure=current_app.config["SESSION_COOKIE_SECURE"],
            samesite="Lax",
            max_age=timeout_minutes * 60,
        )
        return response

    return session
