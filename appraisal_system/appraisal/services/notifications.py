"""Appraisal notifications (FR-16): queued in the outbox, delivered by a separate command.

Emails say what needs doing and link to the app; they never include answers or feedback text.
"""

import smtplib
from datetime import date, timedelta
from email.message import EmailMessage

from common.hph_logging import logging
from models import (
    Appraisal,
    AppraisalStatus,
    Cycle,
    CycleStatus,
    NotificationOutbox,
    NotificationStatus,
    NotificationType,
    ReviewStep,
    StepStatus,
    db,
    utc_now,
)
import config


logger = logging.getLogger(__name__)

TEMPLATES = {
    NotificationType.APPRAISAL_OPENED: (
        "Your appraisal for {cycle_name} is open",
        "Hi {recipient_name},\n\nYour appraisal for {cycle_name} is ready to fill in. "
        "Please submit it by {submission_due}.\n\n{link}\n",
    ),
    NotificationType.REVIEW_REQUESTED: (
        "Appraisal to review: {employee_name}",
        "Hi {recipient_name},\n\n{employee_name}'s appraisal for {cycle_name} is waiting for "
        "your {stage} review. Please complete it by {review_due}.\n\n{link}\n",
    ),
    NotificationType.CHANGES_REQUESTED: (
        "Your appraisal was sent back for changes",
        "Hi {recipient_name},\n\nYour {stage} sent your appraisal for {cycle_name} back with "
        "feedback. Please read it, update your answers and submit again.\n\n{link}\n",
    ),
    NotificationType.APPRAISAL_APPROVED: (
        "Your appraisal for {cycle_name} is approved",
        "Hi {recipient_name},\n\nYour manager approved your appraisal for {cycle_name}. "
        "You can now read the final review.\n\n{link}\n",
    ),
    NotificationType.SUBMISSION_REMINDER: (
        "Reminder: submit your appraisal by {submission_due}",
        "Hi {recipient_name},\n\nYour appraisal for {cycle_name} is due on {submission_due} "
        "and has not been submitted yet.\n\n{link}\n",
    ),
    NotificationType.REVIEW_REMINDER: (
        "Reminder: review {employee_name}'s appraisal by {review_due}",
        "Hi {recipient_name},\n\n{employee_name}'s appraisal for {cycle_name} is still waiting "
        "for your {stage} review, due on {review_due}.\n\n{link}\n",
    ),
}


def appraisal_link(appraisal_id):
    return f"{config.APPRAISAL_APP_URL.rstrip('/')}/{appraisal_id}"


def enqueue(event_type, recipient_user_id, appraisal, extra=None, dedupe_key=None):
    """Queue one email in the current transaction; the caller commits.

    Parameters
    ----------
    event_type : str
        A ``NotificationType`` value.
    recipient_user_id : int
        HPH user to notify; their address is looked up at delivery time.
    appraisal : models.Appraisal
        The appraisal the email is about.
    extra : dict, optional, default = None
        Additional template values (e.g. ``stage``).
    dedupe_key : str, optional, default = None
        Skips queuing when a notification with the same key already exists.
    """
    if dedupe_key is not None:
        exists = (
            db.session.query(NotificationOutbox.id)
            .filter(NotificationOutbox.dedupe_key == dedupe_key)
            .first()
        )
        if exists:
            return False

    cycle = appraisal.cycle
    payload = {
        "employee_name": appraisal.employee_name,
        "cycle_name": cycle.name,
        "submission_due": cycle.submission_due.isoformat(),
        "review_due": cycle.review_due.isoformat(),
        "link": appraisal_link(appraisal.id),
    }
    payload.update(extra or {})

    db.session.add(
        NotificationOutbox(
            event_type=event_type,
            recipient_user_id=recipient_user_id,
            cycle_id=cycle.id,
            appraisal_id=appraisal.id,
            payload=payload,
            dedupe_key=dedupe_key,
        )
    )

    return True


def render(notification, recipient_name):
    subject, body = TEMPLATES[notification.event_type]
    values = dict(notification.payload or {})
    values.setdefault("stage", "")
    values["recipient_name"] = recipient_name

    return subject.format(**values), body.format(**values)


class SmtpSender:
    """Sends plain-text email through the SMTP server in ``config``."""

    def send(self, to_address, subject, body):
        message = EmailMessage()
        message["From"] = config.SMTP_FROM
        message["To"] = to_address
        message["Subject"] = subject
        message.set_content(body)

        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as smtp:
            if config.SMTP_USE_TLS:
                smtp.starttls()
            if config.SMTP_USERNAME:
                smtp.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
            smtp.send_message(message)


def deliver_pending(directory, sender, limit=100):
    """Send queued notifications.

    Parameters
    ----------
    directory : common.directory_client.DirectoryClient
        Supplies recipients' names, addresses and active status.
    sender : object
        Has ``send(to_address, subject, body)``; ``SmtpSender`` in production.
    limit : int, optional, default = 100
        Maximum notifications to process in one run.

    Returns
    -------
    dict
        Counts of ``sent``, ``skipped`` and ``failed`` notifications.
    """
    pending = (
        db.session.query(NotificationOutbox)
        .filter(NotificationOutbox.status == NotificationStatus.PENDING)
        .order_by(NotificationOutbox.id)
        .limit(limit)
        .all()
    )
    counts = {"sent": 0, "skipped": 0, "failed": 0}
    if not pending:
        return counts

    recipients = {
        user.id: user for user in directory.users(ids={item.recipient_user_id for item in pending})
    }

    for notification in pending:
        user = recipients.get(notification.recipient_user_id)
        if user is None or not user.is_active or not user.email:
            notification.status = NotificationStatus.SKIPPED
            notification.last_error = "recipient is inactive or has no email address"
            counts["skipped"] += 1
            continue

        subject, body = render(notification, user.first_name or user.full_name)
        try:
            sender.send(user.email, subject, body)
        except Exception as exc:  # noqa: BLE001 // any delivery failure is retried later
            notification.attempts += 1
            notification.last_error = str(exc)[:1000]
            if notification.attempts >= config.NOTIFICATION_MAX_ATTEMPTS:
                notification.status = NotificationStatus.FAILED
            counts["failed"] += 1
            logger.warning("notification %s failed: %s", notification.id, exc)
            continue

        notification.status = NotificationStatus.SENT
        notification.sent_at = utc_now()
        counts["sent"] += 1

    db.session.commit()

    return counts


def queue_deadline_reminders(today=None, days_before=None):
    """Queue reminders for appraisals and reviews due within *days_before* days (FR-16).

    Idempotent: each appraisal or review step gets at most one reminder per due date.

    Returns
    -------
    dict
        Counts of queued ``submission`` and ``review`` reminders.
    """
    today = today or date.today()
    days_before = config.REMINDER_DAYS_BEFORE if days_before is None else days_before
    window_end = today + timedelta(days=days_before)
    counts = {"submission": 0, "review": 0}

    cycles = db.session.query(Cycle).filter(Cycle.status == CycleStatus.ACTIVE).all()
    for cycle in cycles:
        if today <= cycle.submission_due <= window_end:
            unsubmitted = (
                db.session.query(Appraisal)
                .filter(
                    Appraisal.cycle_id == cycle.id,
                    Appraisal.status.in_(AppraisalStatus.EDITABLE_BY_EMPLOYEE),
                )
                .all()
            )
            for appraisal in unsubmitted:
                key = f"submission_reminder:{appraisal.id}:{cycle.submission_due.isoformat()}"
                if enqueue(
                    NotificationType.SUBMISSION_REMINDER,
                    appraisal.user_id,
                    appraisal,
                    dedupe_key=key,
                ):
                    counts["submission"] += 1

        if today <= cycle.review_due <= window_end:
            active_steps = (
                db.session.query(ReviewStep)
                .join(Appraisal)
                .filter(Appraisal.cycle_id == cycle.id, ReviewStep.status == StepStatus.ACTIVE)
                .all()
            )
            for step in active_steps:
                key = f"review_reminder:{step.id}:{cycle.review_due.isoformat()}"
                if enqueue(
                    NotificationType.REVIEW_REMINDER,
                    step.reviewer_user_id,
                    step.appraisal,
                    {"stage": step.review_stage},
                    dedupe_key=key,
                ):
                    counts["review"] += 1

    db.session.commit()

    return counts
