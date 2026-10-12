"""Microsoft sign-in (FR-19 to FR-21): start it, finish it, and find the HPH user it belongs to."""

import hashlib
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from flask import current_app
from sqlalchemy import func

from app.extensions import db
from app.users.models import User
from appraisal_system.access_bff.entra.client import EntraSignInError
from appraisal_system.access_bff.entra.models import (
    ENTRA_PROVIDER,
    EntraLoginTransaction,
    ExternalIdentity,
    IdentityStatus,
    LinkMethod,
)


TRANSACTION_TTL = timedelta(minutes=10)
MAX_NEXT_PATH_LENGTH = 512
ID_TOKEN_CLOCK_SKEW_SECONDS = 300
# Admin accounts are linked on purpose (`flask entra-link`), never automatically by email.
ADMIN_ROLE_TYPES = frozenset({"super_admin", "admin"})
TENANT_ID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class SignInRefused(Exception):
    """The sign-in cannot complete; ``reason`` is sent to the frontend's login page."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def utc_now():
    return datetime.now(timezone.utc)


def _hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def safe_next_path(value):
    """Keep only a same-site relative path, so the redirect after sign-in cannot leave HPH."""
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or len(value) > MAX_NEXT_PATH_LENGTH
    ):
        return "/"

    return value


def validate_settings(config):
    """Fail at start-up when sign-in is switched on but not fully configured.

    Raises
    ------
    RuntimeError
        Listing every missing or invalid setting.
    """
    problems = []
    if not TENANT_ID_PATTERN.match(config.get("ENTRA_TENANT_ID", "").lower()):
        # A specific tenant, never "common" or "organizations", keeps other organisations out.
        problems.append("ENTRA_TENANT_ID must be HPH's directory (tenant) id, a GUID")
    if not TENANT_ID_PATTERN.match(config.get("ENTRA_CLIENT_ID", "").lower()):
        problems.append("ENTRA_CLIENT_ID must be the app registration's client id, a GUID")
    if not config.get("ENTRA_CLIENT_SECRET") and not (
        config.get("ENTRA_CLIENT_CERTIFICATE_PRIVATE_KEY")
        and config.get("ENTRA_CLIENT_CERTIFICATE_THUMBPRINT")
    ):
        problems.append("set ENTRA_CLIENT_SECRET, or the certificate private key and thumbprint")

    redirect_uri = config.get("ENTRA_REDIRECT_URI", "")
    parts = urlsplit(redirect_uri)
    is_local = parts.hostname in ("localhost", "127.0.0.1")
    if parts.scheme != "https" and not (parts.scheme == "http" and is_local):
        problems.append("ENTRA_REDIRECT_URI must be an https URL (http only for localhost)")
    elif not parts.path.endswith("/auth/microsoft/callback"):
        problems.append("ENTRA_REDIRECT_URI must end with /auth/microsoft/callback")

    if problems:
        raise RuntimeError("Microsoft sign-in is enabled but misconfigured: " + "; ".join(problems))


def client_credential(config):
    """The app registration's credential: a certificate when configured, else the secret."""
    private_key = config.get("ENTRA_CLIENT_CERTIFICATE_PRIVATE_KEY")
    thumbprint = config.get("ENTRA_CLIENT_CERTIFICATE_THUMBPRINT")
    if private_key and thumbprint:
        return {"private_key": private_key.replace("\\n", "\n"), "thumbprint": thumbprint}

    return config.get("ENTRA_CLIENT_SECRET")


def begin_sign_in(client, next_path):
    """Start a sign-in.

    Returns
    -------
    tuple of (str, str)
        The random value for the browser's sign-in cookie, and the Microsoft URL to redirect to.

    Raises
    ------
    SignInRefused
        If Microsoft cannot be reached.
    """
    now = utc_now()
    db.session.query(EntraLoginTransaction).filter(EntraLoginTransaction.expires_at < now).delete()

    try:
        flow = client.begin()
    except EntraSignInError as exc:
        db.session.commit()
        raise SignInRefused(exc.reason) from exc

    cookie_value = secrets.token_urlsafe(32)
    db.session.add(
        EntraLoginTransaction(
            id=_hash(cookie_value),
            flow=flow,
            next_path=safe_next_path(next_path),
            expires_at=now + TRANSACTION_TTL,
        )
    )
    db.session.commit()

    return cookie_value, flow["auth_uri"]


def _take_transaction(cookie_value):
    """Return ``(flow, next_path)`` of the sign-in this browser started, deleting it (single use)."""
    if not cookie_value:
        raise SignInRefused("expired")

    transaction = db.session.get(EntraLoginTransaction, _hash(cookie_value))
    if transaction is None:
        raise SignInRefused("expired")

    flow, next_path, expires_at = transaction.flow, transaction.next_path, transaction.expires_at
    db.session.delete(transaction)
    db.session.commit()

    if expires_at < utc_now():
        raise SignInRefused("expired")

    return flow, next_path


def _is_guest(claims):
    # `acct` is 1 for guests when the app registration emits it; guest UPNs always carry #EXT#.
    username = (claims.get("preferred_username") or "").upper()
    return claims.get("acct") == 1 or "#EXT#" in username


def _link_by_email(claims, tenant_id, object_id):
    """Link a first-time Microsoft account to the HPH user with the same company email."""
    if not current_app.config["ENTRA_LINK_BY_EMAIL"]:
        raise SignInRefused("not_linked")

    email = (claims.get("preferred_username") or claims.get("email") or "").strip().lower()
    if not email:
        raise SignInRefused("not_linked")

    users = User.query.filter(func.lower(User.email) == email).all()
    if len(users) != 1:
        raise SignInRefused("not_linked")

    user = users[0]
    if not user.is_active:
        raise SignInRefused("inactive")
    if user.role.role_type.code in ADMIN_ROLE_TYPES:
        raise SignInRefused("admin_link_required")

    already_linked = ExternalIdentity.query.filter_by(
        user_id=user.id, provider=ENTRA_PROVIDER, status=IdentityStatus.ACTIVE
    ).first()
    if already_linked is not None:
        # A different Microsoft account for the same person (e.g. a recreated account) must be
        # relinked by an administrator, never silently.
        raise SignInRefused("linked_to_another_account")

    identity = ExternalIdentity(
        user=user,
        provider=ENTRA_PROVIDER,
        tenant_id=tenant_id,
        object_id=object_id,
        status=IdentityStatus.ACTIVE,
        link_method=LinkMethod.EMAIL_MATCH,
    )
    db.session.add(identity)
    current_app.logger.info(
        "Linked Microsoft account %s to user %s at first sign-in.", object_id, user.id
    )

    return identity


def _identity_for(claims):
    tenant_id = current_app.config["ENTRA_TENANT_ID"]
    if (claims.get("tid") or "").lower() != tenant_id.lower():
        raise SignInRefused("wrong_tenant")
    if claims.get("exp", 0) + ID_TOKEN_CLOCK_SKEW_SECONDS < time.time():
        raise SignInRefused("invalid_response")

    object_id = claims.get("oid")
    if not object_id:
        raise SignInRefused("invalid_response")
    if _is_guest(claims):
        raise SignInRefused("guest_account")

    identity = ExternalIdentity.query.filter_by(
        provider=ENTRA_PROVIDER, tenant_id=tenant_id, object_id=object_id
    ).one_or_none()
    if identity is None:
        return _link_by_email(claims, tenant_id, object_id)
    if identity.status != IdentityStatus.ACTIVE:
        raise SignInRefused("not_linked")

    return identity


def finish_sign_in(client, cookie_value, auth_response):
    """Finish a sign-in from Microsoft's callback and return the HPH user and where to go next.

    Parameters
    ----------
    client : EntraClient
    cookie_value : str or None
        The browser's sign-in cookie, tying the callback to the browser that started it.
    auth_response : dict
        The callback's query parameters (``code`` and ``state``, or ``error``).

    Returns
    -------
    tuple of (app.users.models.User, str)

    Raises
    ------
    SignInRefused
        With a reason the login page can show.
    """
    flow, next_path = _take_transaction(cookie_value)

    error = auth_response.get("error")
    if error:
        raise SignInRefused("cancelled" if error == "access_denied" else "microsoft_error")

    try:
        claims = client.finish(flow, auth_response)
    except EntraSignInError as exc:
        current_app.logger.warning("Microsoft sign-in failed: %s", exc.detail or exc.reason)
        raise SignInRefused(exc.reason) from exc

    identity = _identity_for(claims)
    if not identity.user.is_active:
        raise SignInRefused("inactive")

    identity.last_authenticated_at = utc_now()
    db.session.commit()

    return identity.user, next_path


def link_identity(email, object_id, tenant_id, replace=False):
    """Link an HPH user to a Microsoft account by hand (admins, recreated accounts).

    Raises
    ------
    ValueError
        If the user does not exist, the account belongs to someone else, or the user is already
        linked to another account and ``replace`` is false.
    """
    user = User.query.filter(func.lower(User.email) == email.strip().lower()).one_or_none()
    if user is None:
        raise ValueError(f"No HPH user has the email {email}.")

    account = ExternalIdentity.query.filter_by(
        provider=ENTRA_PROVIDER, tenant_id=tenant_id, object_id=object_id
    ).one_or_none()
    if account is not None and account.user_id != user.id:
        raise ValueError(
            f"Microsoft account {object_id} is already linked to user {account.user_id}."
        )

    current = ExternalIdentity.query.filter_by(
        user_id=user.id, provider=ENTRA_PROVIDER, status=IdentityStatus.ACTIVE
    ).one_or_none()
    if current is not None and current is not account:
        if not replace:
            raise ValueError(
                f"{email} is already linked to Microsoft account {current.object_id}; "
                "pass --replace to switch."
            )
        current.status = IdentityStatus.REVOKED
        current.revoked_at = utc_now()
        db.session.flush()

    if account is None:
        account = ExternalIdentity(
            user=user, provider=ENTRA_PROVIDER, tenant_id=tenant_id, object_id=object_id
        )
        db.session.add(account)
    account.status = IdentityStatus.ACTIVE
    account.link_method = LinkMethod.ADMIN
    account.revoked_at = None
    db.session.commit()

    return account


def unlink_identity(email):
    """Revoke a user's Microsoft link; they cannot sign in with Microsoft until relinked."""
    user = User.query.filter(func.lower(User.email) == email.strip().lower()).one_or_none()
    if user is None:
        raise ValueError(f"No HPH user has the email {email}.")

    current = ExternalIdentity.query.filter_by(
        user_id=user.id, provider=ENTRA_PROVIDER, status=IdentityStatus.ACTIVE
    ).one_or_none()
    if current is None:
        raise ValueError(f"{email} has no active Microsoft link.")

    current.status = IdentityStatus.REVOKED
    current.revoked_at = utc_now()
    db.session.commit()

    return current
