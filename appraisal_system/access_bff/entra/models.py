"""Microsoft accounts linked to HPH users, and sign-ins in progress."""

from app.extensions import db


ENTRA_PROVIDER = "entra"


class IdentityStatus:
    ACTIVE = "active"
    REVOKED = "revoked"


class LinkMethod:
    # Linked automatically at the person's first Microsoft sign-in, by their company email.
    EMAIL_MATCH = "email_match"
    # Linked by an administrator with `flask entra-link`.
    ADMIN = "admin"


class ExternalIdentity(db.Model):
    """One person's Microsoft account, linked to exactly one HPH user (FR-20, FR-21).

    Identified by tenant and object id, which never change; the email is only used to find the
    HPH user the first time.
    """

    __tablename__ = "external_identities"
    __table_args__ = (
        db.UniqueConstraint(
            "provider", "tenant_id", "object_id", name="uq_external_identities_account"
        ),
        db.Index(
            "uq_external_identities_active_user",
            "user_id",
            "provider",
            unique=True,
            postgresql_where=db.text("status = 'active'"),
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider = db.Column(db.String(32), nullable=False, default=ENTRA_PROVIDER)
    tenant_id = db.Column(db.String(64), nullable=False)
    object_id = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(16), nullable=False, default=IdentityStatus.ACTIVE)
    link_method = db.Column(db.String(16), nullable=False)
    linked_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)
    last_authenticated_at = db.Column(db.DateTime(timezone=True), nullable=True)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)

    user = db.relationship("User")


class EntraLoginTransaction(db.Model):
    """A sign-in between the redirect to Microsoft and its callback (10 minutes at most).

    Keeps MSAL's flow (state, nonce, PKCE verifier) on the server; the browser only holds a
    random cookie whose hash is the key. Deleted as soon as the callback arrives.
    """

    __tablename__ = "entra_login_transactions"

    id = db.Column(db.String(64), primary_key=True)
    flow = db.Column(db.JSON, nullable=False)
    next_path = db.Column(db.String(512), nullable=False, default="/")
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
