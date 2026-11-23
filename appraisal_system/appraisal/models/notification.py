from config_dbname import DB_SCHEMA
from models.base import JSONType, db, utc_now


class NotificationStatus:
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"
    ALL = (PENDING, SENT, FAILED, SKIPPED)


class NotificationType:
    APPRAISAL_OPENED = "appraisal_opened"
    REVIEW_REQUESTED = "review_requested"
    CHANGES_REQUESTED = "changes_requested"
    APPRAISAL_APPROVED = "appraisal_approved"
    SUBMISSION_REMINDER = "submission_reminder"
    REVIEW_REMINDER = "review_reminder"


class NotificationOutbox(db.Model):
    """Emails to send, written in the same transaction as the change that causes them.

    A separate command delivers them, so a mail outage never blocks or loses a review action.
    """

    __tablename__ = "notification_outbox"
    __table_args__ = ({"schema": DB_SCHEMA},)

    id = db.Column(db.Integer, primary_key=True)
    event_type = db.Column(db.String(48), nullable=False)
    recipient_user_id = db.Column(db.Integer, nullable=False)
    cycle_id = db.Column(db.Integer, nullable=True)
    appraisal_id = db.Column(db.Integer, nullable=True)
    payload = db.Column(JSONType, nullable=True)
    status = db.Column(
        db.String(16), nullable=False, default=NotificationStatus.PENDING, index=True
    )
    attempts = db.Column(db.Integer, nullable=False, default=0)
    last_error = db.Column(db.Text, nullable=True)
    # Makes reminder queuing idempotent: one reminder per appraisal or step per due date.
    dedupe_key = db.Column(db.String(200), nullable=True, unique=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    sent_at = db.Column(db.DateTime(timezone=True), nullable=True)
