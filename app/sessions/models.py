from app.extensions import db


class Session(db.Model):
    __tablename__ = "sessions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    # sha256 hex digest of the raw token; the raw token itself is only ever
    # held by the client (httpOnly cookie) and is never persisted.
    session_key = db.Column(db.String(64), unique=True, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)
    last_seen_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    ip_address = db.Column(db.String(45), nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)

    auth_method = db.Column(db.String(16), nullable=False, default="password", server_default="password")

    user = db.relationship("User")

    def __repr__(self):
        return f"<Session user_id={self.user_id}>"
