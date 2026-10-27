import hashlib
import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import UUID

import jwt
import msal
from cryptography.fernet import Fernet
from flask import current_app
from jwt import PyJWKClient

from app.extensions import db
from app.entra.models import ExternalIdentity, IdentityAudit
from app.sessions.models import Session


def uuid_string(value):
    return str(UUID(str(value)))


def validate_config(app):
    origin = urlsplit(app.config["PUBLIC_APP_ORIGIN"])
    if (not origin.netloc or origin.username or origin.password or origin.path or origin.query or origin.fragment
            or origin.scheme not in {"https", "http"}):
        raise ValueError("PUBLIC_APP_ORIGIN must be an origin without a path.")
    if origin.scheme != "https" and origin.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("PUBLIC_APP_ORIGIN must use HTTPS outside localhost.")
    if not app.config["ENTRA_ENABLED"]:
        return
    if origin.scheme == "https" and not app.config["SESSION_COOKIE_SECURE"]:
        raise ValueError("Entra requires secure session cookies on HTTPS origins.")
    for key in ("ENTRA_TENANT_ID", "ENTRA_CLIENT_ID"):
        app.config[key] = uuid_string(app.config[key])
    Fernet(app.config["ENTRA_TRANSACTION_KEY"].encode())
    if app.config["ENTRA_PRIVATE_KEY_FILE"]:
        if not app.config["ENTRA_CERT_THUMBPRINT"]:
            raise ValueError("ENTRA_CERT_THUMBPRINT is required for certificate credentials.")
        Path(app.config["ENTRA_PRIVATE_KEY_FILE"]).read_text()
    elif not app.config["ENTRA_CLIENT_SECRET"]:
        raise ValueError("Entra requires a certificate or a server-side client secret.")


def authority():
    return "https://login.microsoftonline.com/" + current_app.config["ENTRA_TENANT_ID"]


def client():
    config = current_app.config
    credential = config["ENTRA_CLIENT_SECRET"]
    if config["ENTRA_PRIVATE_KEY_FILE"]:
        credential = {"private_key": Path(config["ENTRA_PRIVATE_KEY_FILE"]).read_text(),
                      "thumbprint": config["ENTRA_CERT_THUMBPRINT"]}
    # Per-transaction in-memory token cache is discarded after sign-in.
    return msal.ConfidentialClientApplication(config["ENTRA_CLIENT_ID"], client_credential=credential,
        authority=authority(), timeout=10, exclude_scopes=["offline_access"], enable_pii_log=False)


def encrypt_flow(flow):
    return Fernet(current_app.config["ENTRA_TRANSACTION_KEY"].encode()).encrypt(json.dumps(flow).encode()).decode()


def decrypt_flow(value):
    return json.loads(Fernet(current_app.config["ENTRA_TRANSACTION_KEY"].encode()).decrypt(value.encode()))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def safe_return_to(value):
    value = value or "/"
    decoded = unquote(value)
    if (len(value) > 2048 or not decoded.startswith("/") or decoded.startswith("//")
            or "\\" in decoded or any(ord(c) < 32 or ord(c) == 127 for c in decoded)
            or urlsplit(decoded).scheme or urlsplit(decoded).netloc
            or decoded.split("?", 1)[0].split("#", 1)[0] in {"/login", "/set-password"}
            or decoded.startswith("/auth") or decoded.startswith("/api")):
        return "/"
    return value


@lru_cache(maxsize=8)
def signing_keys(tenant_id):
    return PyJWKClient(f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys", timeout=10)


def validate_id_token(raw_token):
    # In addition to MSAL's state/nonce checks, verify the JWT signature and claims explicitly.
    config = current_app.config
    key = signing_keys(config["ENTRA_TENANT_ID"]).get_signing_key_from_jwt(raw_token).key
    claims = jwt.decode(raw_token, key, algorithms=["RS256"], audience=config["ENTRA_CLIENT_ID"],
        issuer=authority() + "/v2.0", leeway=30,
        options={"require": ["exp", "iat", "nbf", "iss", "aud", "tid", "oid", "nonce"]})
    if uuid_string(claims["tid"]) != config["ENTRA_TENANT_ID"]:
        raise ValueError("Unexpected tenant")
    uuid_string(claims["oid"])
    return claims


def guest_role_allowed(identity, user=None):
    user = user or identity.user
    return identity.account_type != "guest" or (user.role.role_type.code == "client" and user.project_id is not None)


def identity_dict(identity):
    if identity is None:
        return None
    return {"tenant_id": identity.tenant_id, "object_id": identity.object_id,
            "account_type": identity.account_type, "active": identity.active,
            "linked_at": identity.linked_at.isoformat()}


def revoke_sessions(user):
    Session.query.filter_by(user_id=user.id, revoked_at=None).update(
        {"revoked_at": datetime.now(timezone.utc)}, synchronize_session=False)


def link_identity(user, object_id, account_type, actor=None, operator=None):
    tenant_id = uuid_string(current_app.config["ENTRA_TENANT_ID"])
    object_id = uuid_string(object_id)
    if account_type not in {"member", "guest"}:
        raise ValueError("Account type must be member or guest.")
    if account_type == "guest" and (user.role.role_type.code != "client" or user.project_id is None):
        raise ValueError("Guests require the Client role and an assigned project.")
    existing = ExternalIdentity.query.filter_by(tenant_id=tenant_id, object_id=object_id).first()
    if existing and existing.user_id != user.id:
        raise ValueError("That Entra identity is already linked to another HPH account.")
    identity = user.entra_identity
    before = identity_dict(identity)
    if identity is None:
        identity = ExternalIdentity(user=user)
        db.session.add(identity)
    identity.tenant_id, identity.object_id, identity.account_type = tenant_id, object_id, account_type
    identity.active = True
    identity.linked_at = datetime.now(timezone.utc)
    identity.linked_by_user_id = actor.id if actor else None
    db.session.add(IdentityAudit(user_id=user.id, actor_user_id=actor.id if actor else None,
        action="link", operator=operator, details={"before": before, "after": identity_dict(identity)},
        created_at=datetime.now(timezone.utc)))
    revoke_sessions(user)
    return identity


def unlink_identity(user, actor=None, operator=None):
    identity = user.entra_identity
    if identity:
        before = identity_dict(identity)
        identity.active = False
        db.session.add(IdentityAudit(user_id=user.id, actor_user_id=actor.id if actor else None,
            action="unlink", operator=operator, details={"before": before}, created_at=datetime.now(timezone.utc)))
        revoke_sessions(user)
