from app.extensions import db


class ExternalIdentity(db.Model):
    __tablename__ = "external_identities"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True)
    tenant_id = db.Column(db.String(36), nullable=False)
    object_id = db.Column(db.String(36), nullable=False)
    account_type = db.Column(db.String(16), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    linked_at = db.Column(db.DateTime(timezone=True), nullable=False)
    linked_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    last_authenticated_at = db.Column(db.DateTime(timezone=True), nullable=True)
    user = db.relationship("User", foreign_keys=[user_id], backref=db.backref("entra_identity", uselist=False))
    __table_args__ = (
        db.UniqueConstraint("tenant_id", "object_id", name="uq_external_identity_subject"),
        db.CheckConstraint("account_type IN ('member', 'guest')", name="ck_identity_account_type"),
    )


class IdentityAudit(db.Model):
    __tablename__ = "identity_audits"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    action = db.Column(db.String(16), nullable=False)
    operator = db.Column(db.String(128), nullable=True)
    details = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False)


class AuthTransaction(db.Model):
    __tablename__ = "auth_transactions"
    state_hash = db.Column(db.String(64), primary_key=True)
    browser_hash = db.Column(db.String(64), nullable=False)
    encrypted_flow = db.Column(db.Text, nullable=False)
    return_to = db.Column(db.String(2048), nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
