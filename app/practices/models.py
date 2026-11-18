"""Practice master list, per-person practice access, status history and
coders' "please add this practice" requests.

A practice is never deleted when it closes - its status moves to
Terminated (which also resolves its open IT tickets, see
app/practices/services.change_status). Delete is only for a row created by
mistake that has no tickets.
"""
from app.extensions import db

PRACTICE_STATUSES = ("Active", "Inactive", "Terminated")
ACCESS_STATUSES = (
    "Request to Practice Sent",
    "Credentials Received from Practice",
    "Credentials Shared with User",
    "Access Live",
    "IT Ticket Received",
    "Not Found",
    "Request Cancelled",
    "Access Closed",
)
REQUEST_STATUSES = ("pending", "assigned", "created", "rejected")


def _sql_in(values):
    return ", ".join("'" + value.replace("'", "''") + "'" for value in values)


def practice_name_key(name):
    """Case- and whitespace-insensitive matching key for practice names."""
    return " ".join((name or "").split()).lower()


class Practice(db.Model):
    __tablename__ = "practices"
    __table_args__ = (
        db.CheckConstraint(f"status IN ({_sql_in(PRACTICE_STATUSES)})", name="ck_practices_status"),
        db.CheckConstraint("cms_count IS NULL OR cms_count >= 0", name="ck_practices_cms_count"),
        db.Index("ix_practices_project_status", "project_id", "status"),
    )

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    name = db.Column(db.String(255), nullable=False)
    name_key = db.Column(db.String(255), nullable=False, index=True)
    tin = db.Column(db.String(32), nullable=True, unique=True)
    worktype = db.Column(db.String(64), nullable=True)
    priority_rank = db.Column(db.Integer, nullable=True)
    cms_count = db.Column(db.Integer, nullable=True)
    emr = db.Column(db.String(255), nullable=True)
    affiliation = db.Column(db.String(255), nullable=True)
    pipeline_2027 = db.Column(db.String(128), nullable=True)
    filter_72 = db.Column(db.String(16), nullable=True)
    onshore_coders = db.Column(db.String(500), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(16), nullable=False, default="Active", server_default="Active")
    status_effective_from = db.Column(db.Date, nullable=True)
    status_reason = db.Column(db.String(500), nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), onupdate=db.func.now(), nullable=False)

    assignments = db.relationship(
        "PracticeAssignment", back_populates="practice", cascade="all, delete-orphan",
        order_by="PracticeAssignment.id",
    )

    def __repr__(self):
        return f"<Practice {self.id} {self.name!r} {self.status}>"


class PracticeStatusChange(db.Model):
    __tablename__ = "practice_status_history"

    id = db.Column(db.Integer, primary_key=True)
    practice_id = db.Column(db.Integer, db.ForeignKey("practices.id", ondelete="CASCADE"), nullable=False, index=True)
    from_status = db.Column(db.String(16), nullable=True)
    to_status = db.Column(db.String(16), nullable=False)
    effective_from = db.Column(db.Date, nullable=False)
    reason = db.Column(db.String(500), nullable=True)
    auto_resolved_count = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)

    actor = db.relationship("User")


class PracticeAssignment(db.Model):
    """One person's access to one practice. The person's role (Coder (1LR)
    or QA (2LR)) always comes from users.role_id and is never stored here."""

    __tablename__ = "practice_assignments"
    __table_args__ = (
        db.UniqueConstraint("practice_id", "user_id", name="uq_practice_assignments_practice_user"),
        db.CheckConstraint(
            f"access_status IS NULL OR access_status IN ({_sql_in(ACCESS_STATUSES)})",
            name="ck_practice_assignments_access_status",
        ),
        db.Index("ix_practice_assignments_user", "user_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    practice_id = db.Column(db.Integer, db.ForeignKey("practices.id", ondelete="CASCADE"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    access_status = db.Column(db.String(64), nullable=True)
    access_status_since = db.Column(db.Date, nullable=True)
    assigned_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), onupdate=db.func.now(), nullable=False)

    practice = db.relationship("Practice", back_populates="assignments")
    user = db.relationship("User", foreign_keys=[user_id])


class PracticeRequest(db.Model):
    __tablename__ = "practice_requests"
    __table_args__ = (
        db.CheckConstraint(f"status IN ({_sql_in(REQUEST_STATUSES)})", name="ck_practice_requests_status"),
        db.Index("ix_practice_requests_status", "status"),
    )

    id = db.Column(db.Integer, primary_key=True)
    requested_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    tin = db.Column(db.String(32), nullable=True)
    note = db.Column(db.String(1000), nullable=True)
    status = db.Column(db.String(16), nullable=False, default="pending", server_default="pending")
    practice_id = db.Column(db.Integer, db.ForeignKey("practices.id", ondelete="SET NULL"), nullable=True)
    resolved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    resolved_at = db.Column(db.DateTime(timezone=True), nullable=True)
    resolution_note = db.Column(db.String(1000), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)

    requested_by = db.relationship("User", foreign_keys=[requested_by_id])
    resolved_by = db.relationship("User", foreign_keys=[resolved_by_id])
    practice = db.relationship("Practice")
