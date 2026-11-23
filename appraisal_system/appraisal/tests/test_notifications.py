from datetime import date

import pytest
from conftest import EMPLOYEE_ID, LEAD_ID, MANAGER_ID, directory_user
from helpers import (
    appraisal_id_for,
    approve_as_manager,
    complete_lead,
    launched_cycle,
    post,
    submit_filled,
)

from models import NotificationOutbox, NotificationStatus, db
from services import notifications


class RecordingSender:
    def __init__(self, fail_for=()):
        self.sent = []
        self.fail_for = set(fail_for)

    def send(self, to_address, subject, body):
        if to_address in self.fail_for:
            raise ConnectionError("smtp down")
        self.sent.append((to_address, subject, body))


def _queued(event_type=None):
    query = db.session.query(NotificationOutbox).order_by(NotificationOutbox.id)
    if event_type:
        query = query.filter(NotificationOutbox.event_type == event_type)
    return query.all()


class TestQueuedByTheFlow:
    def test_each_step_notifies_the_next_person(self, client, tokens):
        launched_cycle(client, tokens)
        appraisal_id = appraisal_id_for(EMPLOYEE_ID)
        submit_filled(client, tokens, appraisal_id)
        complete_lead(client, tokens, appraisal_id)
        approve_as_manager(client, tokens, appraisal_id)

        flow = [
            (row.event_type, row.recipient_user_id, (row.payload or {}).get("stage"))
            for row in _queued()
            if row.appraisal_id == appraisal_id
        ]
        assert flow == [
            ("appraisal_opened", EMPLOYEE_ID, None),
            ("review_requested", LEAD_ID, "lead"),
            ("review_requested", MANAGER_ID, "manager"),
            ("appraisal_approved", EMPLOYEE_ID, None),
        ]

    def test_send_back_notifies_the_employee_without_the_feedback_text(self, client, tokens):
        launched_cycle(client, tokens)
        appraisal_id = appraisal_id_for(EMPLOYEE_ID)
        submit_filled(client, tokens, appraisal_id)
        post(client, tokens.lead(), appraisal_id, "request-changes", {"feedback": "Private note"})

        row = _queued("changes_requested")[0]

        assert row.recipient_user_id == EMPLOYEE_ID
        assert "Private note" not in str(row.payload)


class TestDelivery:
    def test_sends_skips_and_retries(self, client, tokens, directory, flask_app, monkeypatch):
        monkeypatch.setattr(notifications.config, "NOTIFICATION_MAX_ATTEMPTS", 2)
        launched_cycle(client, tokens)
        directory.add(directory_user(31, "employee", reports_to_id=LEAD_ID, is_active=False))
        sender = RecordingSender(fail_for={"user30@example.com"})

        counts = notifications.deliver_pending(directory, sender)

        assert counts["failed"] == 1
        assert counts["skipped"] == 1
        assert counts["sent"] == len(_queued()) - 2
        failed = next(row for row in _queued() if row.recipient_user_id == EMPLOYEE_ID)
        assert failed.status == NotificationStatus.PENDING
        assert failed.attempts == 1

        notifications.deliver_pending(directory, sender)
        assert failed.status == NotificationStatus.FAILED

    def test_email_links_to_the_appraisal(self, client, tokens, directory):
        launched_cycle(client, tokens)
        sender = RecordingSender()

        notifications.deliver_pending(directory, sender)

        to_address, subject, body = next(
            item for item in sender.sent if item[0] == "user30@example.com"
        )
        assert subject == "Your appraisal for FY2026 annual is open"
        assert f"/{appraisal_id_for(EMPLOYEE_ID)}" in body


class TestReminders:
    @pytest.mark.parametrize(
        "today, expected",
        [(date(2026, 12, 12), 4), (date(2026, 12, 1), 0), (date(2026, 12, 16), 0)],
    )
    def test_submission_reminders_inside_the_window(self, client, tokens, today, expected):
        launched_cycle(client, tokens)

        counts = notifications.queue_deadline_reminders(today=today, days_before=3)

        # Four appraisals can still be submitted; the two waiting for a reviewer cannot.
        assert counts["submission"] == expected

    def test_reminders_are_queued_once(self, client, tokens):
        launched_cycle(client, tokens)

        notifications.queue_deadline_reminders(today=date(2026, 12, 13), days_before=3)
        second = notifications.queue_deadline_reminders(today=date(2026, 12, 14), days_before=3)

        assert second == {"submission": 0, "review": 0}

    def test_review_reminders_go_to_the_current_reviewer(self, client, tokens):
        launched_cycle(client, tokens)
        submit_filled(client, tokens, appraisal_id_for(EMPLOYEE_ID))

        counts = notifications.queue_deadline_reminders(today=date(2026, 12, 29), days_before=3)

        assert counts["review"] == 1
        assert _queued("review_reminder")[0].recipient_user_id == LEAD_ID
