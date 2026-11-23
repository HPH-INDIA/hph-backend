"""Browser routes for Microsoft sign-in. They live outside /api: they are top-level page
navigations, not encrypted JSON calls, and must work before an HPH session exists.
"""

from urllib.parse import urlencode

from flask import abort, current_app, redirect, request

from app.sessions.services import issue_session
from appraisal_system.access_bff.entra import bp
from appraisal_system.access_bff.entra.services import (
    TRANSACTION_TTL,
    SignInRefused,
    begin_sign_in,
    finish_sign_in,
)


TRANSACTION_COOKIE = "entra_sign_in"
COOKIE_PATH = "/auth/microsoft"


def _require_enabled():
    if not current_app.config["ENTRA_SIGN_IN_ENABLED"]:
        abort(404)


def _login_page_with_error(reason):
    query = urlencode({"sso_error": reason})
    return redirect(f"{current_app.config['FRONTEND_LOGIN_URL']}?{query}")


@bp.get("/login")
def login():
    """Send the browser to Microsoft; ``?next=/path`` is where to land after sign-in."""
    _require_enabled()

    try:
        cookie_value, microsoft_url = begin_sign_in(
            current_app.extensions["entra_client"], request.args.get("next")
        )
    except SignInRefused as refused:
        current_app.logger.warning("Microsoft sign-in could not start: %s", refused.reason)
        return _login_page_with_error(refused.reason)

    response = redirect(microsoft_url)
    response.set_cookie(
        TRANSACTION_COOKIE,
        cookie_value,
        max_age=int(TRANSACTION_TTL.total_seconds()),
        httponly=True,
        secure=current_app.config["SESSION_COOKIE_SECURE"],
        # Lax is sent on Microsoft's top-level redirect back to the callback.
        samesite="Lax",
        path=COOKIE_PATH,
    )
    response.headers["Cache-Control"] = "no-store"

    return response


@bp.get("/callback")
def callback():
    """Microsoft redirects here; on success the user gets the normal HPH session cookie."""
    _require_enabled()

    try:
        user, next_path = finish_sign_in(
            current_app.extensions["entra_client"],
            request.cookies.get(TRANSACTION_COOKIE),
            request.args.to_dict(),
        )
    except SignInRefused as refused:
        current_app.logger.info("Microsoft sign-in refused: %s", refused.reason)
        response = _login_page_with_error(refused.reason)
    else:
        issue_session(user)
        response = redirect(f"{current_app.config['FRONTEND_APP_URL'].rstrip('/')}{next_path}")

    response.delete_cookie(TRANSACTION_COOKIE, path=COOKIE_PATH)
    # The URL carried a one-time code; keep the page out of every cache.
    response.headers["Cache-Control"] = "no-store"

    return response
