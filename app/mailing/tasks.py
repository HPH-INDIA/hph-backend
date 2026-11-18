from app.extensions import celery, db
from app.mailing.models import EmailLog
from app.mailing.providers import deliver


def _log(recipient, template_name, status, provider_message_id, error_message):
    db.session.add(
        EmailLog(
            recipient=recipient,
            template_name=template_name,
            status=status,
            provider_message_id=provider_message_id,
            error_message=error_message,
        )
    )
    db.session.commit()


@celery.task(bind=True, name="mailing.send_email", max_retries=3, default_retry_delay=30)
def send_email_task(self, recipient, subject, html_body, text_body, template_name):
    """Performs the actual send through the configured EMAIL_PROVIDER, with
    retry/backoff on transient failures (§5) — the thing the request itself must never wait on.
    EmailLog records the outcome either way; never the message content
    itself (e.g. a temp password), only metadata about the attempt.
    """
    try:
        message_id = deliver(recipient, subject, html_body, text_body)
    except Exception as exc:
        _log(recipient, template_name, "failed", None, str(exc))
        raise self.retry(exc=exc)

    _log(recipient, template_name, "sent", message_id, None)
