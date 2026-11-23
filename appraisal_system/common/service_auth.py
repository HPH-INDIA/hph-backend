"""Short-lived signed tokens between the Access/BFF and the HPH microservices.

The BFF signs a *user* token for every request it forwards, carrying the signed-in user's HPH
identity and feature permissions. Services sign *service* tokens when they call each other. Every
process holds its own Ed25519 private key and trusts other issuers only by their public keys, so
a service that can verify BFF tokens cannot mint them.

Run ``python -m common.service_auth`` to print a new key pair.
"""

import json
import os
import time
import uuid
from dataclasses import dataclass

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import errors


ALGORITHM = "EdDSA"
TOKEN_TTL_SECONDS = 60
# Rejects tokens an issuer minted with a long lifetime, so a leaked token stays short-lived.
MAX_TOKEN_LIFETIME_SECONDS = 300
CLOCK_LEEWAY_SECONDS = 10

USER_TOKEN = "user"
SERVICE_TOKEN = "service"
TOKEN_TYPES = (USER_TOKEN, SERVICE_TOKEN)

# caller_id header value that marks a trusted service caller.
SERVICE_CALLER_ID = -1


@dataclass(frozen=True)
class Caller:
    """The verified identity behind a request.

    Parameters
    ----------
    user_id : int
        HPH ``users.id`` for a user token, ``SERVICE_CALLER_ID`` for a service token.
    issuer : str
        Name of the process that signed the token (e.g. ``hph-bff``).
    token_type : str
        ``"user"`` or ``"service"``.
    role_type : str, optional, default = None
        HPH role type code of the user (``employee``, ``lead``, ``manager``, ``admin``, ...).
    project_id : int, optional, default = None
        The user's project.
    permissions : frozenset of str, optional, default = frozenset()
        Feature grants as ``"<codename>:read"`` / ``"<codename>:write"``.
    """

    user_id: int
    issuer: str
    token_type: str
    role_type: str = None
    project_id: int = None
    permissions: frozenset = frozenset()

    @property
    def is_service(self):
        return self.token_type == SERVICE_TOKEN

    def can(self, feature, access="read"):
        """Return whether the caller holds *feature* with *access* (write implies read)."""
        if self.is_service:
            return False

        if access == "write":
            return f"{feature}:write" in self.permissions

        return f"{feature}:read" in self.permissions or f"{feature}:write" in self.permissions


@dataclass(frozen=True)
class TrustedIssuer:
    name: str
    public_key: object
    token_types: frozenset


def _load_pem(value):
    # Env vars and dashboards often flatten PEM newlines into a literal "\n".
    return value.replace("\\n", "\n").encode()


def load_private_key(pem):
    return serialization.load_pem_private_key(_load_pem(pem), password=None)


def load_public_key(pem):
    return serialization.load_pem_public_key(_load_pem(pem))


def parse_trusted_issuers(raw):
    """Parse the ``SERVICE_AUTH_TRUSTED_ISSUERS`` JSON document.

    Parameters
    ----------
    raw : str or dict
        ``{"<issuer>": {"publicKey": "<PEM>", "tokenTypes": ["user" | "service", ...]}}``.

    Returns
    -------
    dict of str to TrustedIssuer

    Raises
    ------
    ValueError
        If the document is malformed or names an unknown token type.
    """
    document = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(document, dict):
        raise ValueError("trusted issuers must be a JSON object keyed by issuer name")

    trusted = {}
    for name, entry in document.items():
        token_types = frozenset(entry.get("tokenTypes") or [])
        unknown = token_types - set(TOKEN_TYPES)
        if not token_types or unknown:
            raise ValueError(f"issuer {name} must list tokenTypes from {TOKEN_TYPES}")

        trusted[name] = TrustedIssuer(
            name=name, public_key=load_public_key(entry["publicKey"]), token_types=token_types
        )

    return trusted


class ServiceAuth:
    """Mints this process's tokens and verifies tokens addressed to it.

    Parameters
    ----------
    issuer : str
        Name this process signs as.
    private_key : Ed25519PrivateKey or None
        Signing key; ``None`` for a process that only verifies.
    audience : str or None
        Name tokens must be addressed to; ``None`` for a process that only mints.
    trusted_issuers : dict of str to TrustedIssuer
        Issuers whose tokens this process accepts, and which token types each may send.
    """

    def __init__(self, issuer, private_key, audience, trusted_issuers):
        self.issuer = issuer
        self.private_key = private_key
        self.audience = audience
        self.trusted_issuers = trusted_issuers

    @classmethod
    def from_env(cls, audience=None, environ=None):
        """Build from ``SERVICE_AUTH_ISSUER``, ``SERVICE_AUTH_PRIVATE_KEY`` and
        ``SERVICE_AUTH_TRUSTED_ISSUERS``; missing values disable the matching capability."""
        environ = os.environ if environ is None else environ

        private_key_pem = environ.get("SERVICE_AUTH_PRIVATE_KEY")
        trusted_raw = environ.get("SERVICE_AUTH_TRUSTED_ISSUERS")

        return cls(
            issuer=environ.get("SERVICE_AUTH_ISSUER"),
            private_key=load_private_key(private_key_pem) if private_key_pem else None,
            audience=audience,
            trusted_issuers=parse_trusted_issuers(trusted_raw) if trusted_raw else {},
        )

    def _mint(self, audience, subject, token_type, extra_claims=None, ttl=TOKEN_TTL_SECONDS):
        if self.private_key is None or not self.issuer:
            raise RuntimeError("SERVICE_AUTH_ISSUER and SERVICE_AUTH_PRIVATE_KEY must be set.")

        issued_at = int(time.time())
        claims = {
            "iss": self.issuer,
            "aud": audience,
            "sub": subject,
            "typ": token_type,
            "iat": issued_at,
            "exp": issued_at + ttl,
            "jti": uuid.uuid4().hex,
        }
        claims.update(extra_claims or {})

        return jwt.encode(claims, self.private_key, algorithm=ALGORITHM)

    def mint_user_token(self, audience, user_id, role_type, project_id, permissions):
        """Sign a token that lets *audience* act for an HPH user.

        Parameters
        ----------
        audience : str
            Service the token is for (e.g. ``hph-appraisal``).
        user_id : int
            HPH ``users.id``.
        role_type : str
            The user's role type code.
        project_id : int or None
            The user's project.
        permissions : iterable of str
            ``"<codename>:read"`` / ``"<codename>:write"`` grants.

        Returns
        -------
        str
            Compact JWT valid for ``TOKEN_TTL_SECONDS``.
        """
        hph_claims = {
            "uid": int(user_id),
            "rt": role_type,
            "pid": project_id,
            "perm": sorted(set(permissions)),
        }

        return self._mint(audience, f"user:{user_id}", USER_TOKEN, {"hph": hph_claims})

    def mint_service_token(self, audience):
        """Sign a token identifying this process to *audience*."""
        return self._mint(audience, f"service:{self.issuer}", SERVICE_TOKEN)

    def verify(self, token):
        """Verify *token* and return the caller it identifies.

        Raises
        ------
        common.errors.Unauthenticated
            If the token is missing, malformed, expired, addressed elsewhere, signed by an
            untrusted issuer, or of a type its issuer may not send.
        """
        if not token:
            raise errors.Unauthenticated("missing bearer token")

        if not self.audience:
            raise errors.Unauthenticated("this service does not accept tokens")

        try:
            unverified = jwt.decode(token, options={"verify_signature": False})
        except jwt.InvalidTokenError as exc:
            raise errors.Unauthenticated(f"malformed token: {exc}") from exc

        trusted = self.trusted_issuers.get(unverified.get("iss"))
        if trusted is None:
            raise errors.Unauthenticated("token issuer is not trusted")

        try:
            claims = jwt.decode(
                token,
                trusted.public_key,
                algorithms=[ALGORITHM],
                audience=self.audience,
                issuer=trusted.name,
                leeway=CLOCK_LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.InvalidTokenError as exc:
            raise errors.Unauthenticated(f"invalid token: {exc}") from exc

        if claims["exp"] - claims["iat"] > MAX_TOKEN_LIFETIME_SECONDS:
            raise errors.Unauthenticated("token lifetime is too long")

        token_type = claims.get("typ")
        if token_type not in trusted.token_types:
            raise errors.Unauthenticated(f"{trusted.name} may not send {token_type} tokens")

        if token_type == SERVICE_TOKEN:
            return Caller(user_id=SERVICE_CALLER_ID, issuer=trusted.name, token_type=SERVICE_TOKEN)

        hph_claims = claims.get("hph") or {}
        try:
            user_id = int(hph_claims["uid"])
        except (KeyError, TypeError, ValueError) as exc:
            raise errors.Unauthenticated("user token has no HPH user id") from exc

        return Caller(
            user_id=user_id,
            issuer=trusted.name,
            token_type=USER_TOKEN,
            role_type=hph_claims.get("rt"),
            project_id=hph_claims.get("pid"),
            permissions=frozenset(hph_claims.get("perm") or []),
        )


def bearer_token(authorization_header):
    """Return the token from an ``Authorization: Bearer <token>`` header, or ``None``."""
    if not authorization_header:
        return None

    scheme, _, token = authorization_header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None

    return token.strip()


def generate_key_pair():
    """Return a new ``(private_pem, public_pem)`` Ed25519 key pair as strings."""
    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )

    return private_pem, public_pem


if __name__ == "__main__":
    private_pem, public_pem = generate_key_pair()
    print("# SERVICE_AUTH_PRIVATE_KEY (keep secret, this process only)")
    print(private_pem)
    print("# publicKey for SERVICE_AUTH_TRUSTED_ISSUERS of the processes that trust this one")
    print(public_pem)
