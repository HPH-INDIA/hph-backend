import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from cryptography.fernet import InvalidToken
from flask import current_app, redirect, request
from jwt import PyJWTError
from requests.exceptions import RequestException
from sqlalchemy import delete

from app.entra import bp
from app.entra.models import AuthTransaction, ExternalIdentity
from app.entra.services import (client, decrypt_flow, digest, encrypt_flow, guest_role_allowed,
    pilot_identity_allowed, safe_return_to, uuid_string, validate_id_token)
from app.extensions import db
from app.sessions.services import issue_session

TRANSACTION_COOKIE = "hph_entra_transaction"


def clean_response(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def failure(code):
    # Only fixed codes reach the SPA; never reflect provider error descriptions.
    current_app.logger.warning("entra_login outcome=%s", code)
    response = redirect(current_app.config["PUBLIC_APP_ORIGIN"] + "/login?" + urlencode({"auth_error": code}), 303)
    response.delete_cookie(TRANSACTION_COOKIE, path="/auth", secure=current_app.config["SESSION_COOKIE_SECURE"], httponly=True, samesite="Lax")
    return clean_response(response)


@bp.get("/options")
def options():
    return clean_response(current_app.make_response({"microsoftEnabled": current_app.config["ENTRA_ENABLED"]}))


@bp.get("/login")
def login():
    if not current_app.config["ENTRA_ENABLED"]:
        return failure("microsoft_unavailable")
    now = datetime.now(timezone.utc)
    AuthTransaction.query.filter(AuthTransaction.expires_at <= now).delete(synchronize_session=False)
    old_browser = request.cookies.get(TRANSACTION_COOKIE)
    if old_browser:
        AuthTransaction.query.filter_by(browser_hash=digest(old_browser)).delete(synchronize_session=False)
    try:
        flow = client().initiate_auth_code_flow(scopes=[],
            redirect_uri=current_app.config["PUBLIC_APP_ORIGIN"] + "/auth/callback",
            prompt="select_account", response_mode="query")
        browser = secrets.token_urlsafe(32)
        db.session.add(AuthTransaction(state_hash=digest(flow["state"]), browser_hash=digest(browser),
            encrypted_flow=encrypt_flow(flow), return_to=safe_return_to(request.args.get("return_to")),
            expires_at=now + timedelta(minutes=10)))
        db.session.commit()
    except (ValueError, KeyError, RequestException):
        db.session.rollback()
        return failure("microsoft_unavailable")
    response = redirect(flow["auth_uri"])
    response.set_cookie(TRANSACTION_COOKIE, browser, max_age=600, path="/auth", httponly=True,
        secure=current_app.config["SESSION_COOKIE_SECURE"], samesite="Lax")
    return clean_response(response)


@bp.get("/callback")
def callback():
    if not current_app.config["ENTRA_ENABLED"]:
        return failure("microsoft_unavailable")
    state, browser = request.args.get("state"), request.cookies.get(TRANSACTION_COOKIE)
    if not state or not browser or any(len(v) != 1 for _, v in request.args.lists()):
        return failure("invalid_transaction")
    # DELETE RETURNING consumes the transaction atomically across all workers.
    transaction = db.session.execute(delete(AuthTransaction).where(
        AuthTransaction.state_hash == digest(state), AuthTransaction.browser_hash == digest(browser),
        AuthTransaction.expires_at > datetime.now(timezone.utc)).returning(AuthTransaction.encrypted_flow, AuthTransaction.return_to)).mappings().first()
    db.session.commit()
    if transaction is None:
        return failure("invalid_transaction")
    if "error" in request.args:
        return failure("cancelled" if request.args["error"] == "access_denied" else "microsoft_unavailable")
    try:
        flow = decrypt_flow(transaction["encrypted_flow"])
        result = client().acquire_token_by_auth_code_flow(flow, request.args.to_dict())
        if "error" in result or "id_token" not in result:
            return failure("access_denied")
        claims = validate_id_token(result["id_token"])
        # MSAL validates its nonce representation; don't implement nonce hashing ourselves.
        identity = ExternalIdentity.query.filter_by(tenant_id=uuid_string(claims["tid"]),
            object_id=uuid_string(claims["oid"]), active=True).with_for_update().first()
        if identity is None or not identity.user.is_active or not identity.user.role.is_active:
            return failure("account_unavailable")
        if not guest_role_allowed(identity) or not pilot_identity_allowed(identity):
            return failure("access_denied")
        # Optional acct claim can tighten guest protection when configured by IT.
        if str(claims.get("acct", "")) == "1" and identity.account_type != "guest":
            return failure("access_denied")
        identity.last_authenticated_at = datetime.now(timezone.utc)
        response = redirect(current_app.config["PUBLIC_APP_ORIGIN"] + safe_return_to(transaction["return_to"]), 303)
        # issue_session's after_request hook sets the HPH cookie on this redirect.
        issue_session(identity.user, auth_method="entra")
        response.delete_cookie(TRANSACTION_COOKIE, path="/auth", secure=current_app.config["SESSION_COOKIE_SECURE"], httponly=True, samesite="Lax")
        current_app.logger.info("entra_login outcome=success user_id=%s", identity.user_id)
        return clean_response(response)
    except (ValueError, RuntimeError, KeyError, TypeError, InvalidToken, PyJWTError):
        db.session.rollback()
        return failure("invalid_response")
    except RequestException:
        db.session.rollback()
        return failure("microsoft_unavailable")
