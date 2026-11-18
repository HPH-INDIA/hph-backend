"""IT tickets: one row per Jira ticket (VTEC-n) a Coder (1LR) or QA (2LR)
raised because a practice blocked their work, plus the audit trail and
the link between full-idle manual records and the tickets that caused them.
"""
from app.extensions import db

TICKET_STATUSES = ("in_progress", "resolved")
TICKET_SOURCES = ("manual", "import")
JIRA_KEY_PATTERN = r"^VTEC-[0-9]+$"


class IssueCategory(db.Model):
    __tablename__ = "issue_categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False, unique=True)
    active = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)


class ItTicket(db.Model):
    __tablename__ = "it_tickets"
    __table_args__ = (
        db.CheckConstraint("status IN ('in_progress', 'resolved')", name="ck_it_tickets_status"),
        db.CheckConstraint("source IN ('manual', 'import')", name="ck_it_tickets_source"),
        # ck_it_tickets_jira_key (jira_key ~ JIRA_KEY_PATTERN) lives in the
        # migration only: it is PostgreSQL syntax and models must stay portable.
        db.CheckConstraint("charts_available IS NULL OR charts_available >= 0", name="ck_it_tickets_charts"),
        db.CheckConstraint("resolved_on IS NULL OR resolved_on >= opened_on", name="ck_it_tickets_resolved_after_opened"),
        db.Index("ix_it_tickets_user_status", "user_id", "status"),
        db.Index("ix_it_tickets_practice_status", "practice_id", "status"),
        db.Index("ix_it_tickets_opened_on", "opened_on"),
    )

    id = db.Column(db.Integer, primary_key=True)
    jira_key = db.Column(db.String(32), nullable=False, unique=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    practice_id = db.Column(db.Integer, db.ForeignKey("practices.id"), nullable=False)
    issue_category_id = db.Column(db.Integer, db.ForeignKey("issue_categories.id"), nullable=True)
    hph_status_note = db.Column(db.Text, nullable=True)
    opened_on = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(16), nullable=False, default="in_progress", server_default="in_progress")
    resolved_on = db.Column(db.Date, nullable=True)
    resolution_note = db.Column(db.String(500), nullable=True)
    charts_available = db.Column(db.Integer, nullable=True)
    not_in_kairon = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    charts_updated_at = db.Column(db.DateTime(timezone=True), nullable=True)
    follow_up_issue = db.Column(db.Text, nullable=True)
    source = db.Column(db.String(16), nullable=False, default="manual", server_default="manual")
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), onupdate=db.func.now(), nullable=False)

    user = db.relationship("User", foreign_keys=[user_id])
    practice = db.relationship("Practice")
    issue_category = db.relationship("IssueCategory")


class ItTicketEvent(db.Model):
    """Audit trail. ticket_id is deliberately not a foreign key so the
    history (including the delete itself) survives the ticket's deletion."""

    __tablename__ = "it_ticket_events"
    __table_args__ = (db.Index("ix_it_ticket_events_ticket", "ticket_id", "created_at"),)

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, nullable=False)
    jira_key = db.Column(db.String(32), nullable=False)
    action = db.Column(db.String(32), nullable=False)
    changes = db.Column(db.JSON, nullable=False, default=dict)
    reason = db.Column(db.Text, nullable=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)

    actor = db.relationship("User")


class ManualRecordTicket(db.Model):
    """Idle hours of one manual daily record attributed to one ticket."""

    __tablename__ = "manual_record_tickets"
    __table_args__ = (db.CheckConstraint("hours >= 0 AND hours <= 10", name="ck_manual_record_tickets_hours"),)

    manual_record_id = db.Column(
        db.Integer, db.ForeignKey("manual_daily_records.id", ondelete="CASCADE"), primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("it_tickets.id", ondelete="CASCADE"), primary_key=True, index=True)
    hours = db.Column(db.Numeric(4, 2), nullable=False)

    ticket = db.relationship("ItTicket", lazy="joined")
    manual_record = db.relationship(
        "ManualDailyRecord",
        backref=db.backref("ticket_links", lazy="selectin", cascade="all, delete-orphan", passive_deletes=True),
    )
