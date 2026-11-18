"""Outbound mail providers.

`EMAIL_PROVIDER` selects how `send_email_task` delivers a message:

- ``smtp`` (default): Flask-Mail over SMTP — local Mailpit or any relay.
- ``sendgrid``: SendGrid's v3 Web API over HTTPS (port 443). Preferred on
  hosts that block outbound SMTP ports, and it returns SendGrid's own
  message id for tracing a delivery in the SendGrid activity feed.

Each provider returns the message id to record in EmailLog, or raises on
failure so the Celery task can log and retry.
"""

import json
import urllib.error
import urllib.request
from email.utils import parseaddr

from flask import current_app
from flask_mail import Message

from app.extensions import mail

SENDGRID_SEND_URL = "https://api.sendgrid.com/v3/mail/send"
SENDGRID_TIMEOUT_SECONDS = 15


class EmailDeliveryError(RuntimeError):
    """A provider rejected or failed to accept a message."""


def send_via_smtp(recipient, subject, html_body, text_body, sender):
    message = Message(
        subject=subject,
        recipients=[recipient],
        body=text_body,
        html=html_body,
        sender=sender,
    )
    mail.send(message)
    return message.msgId


def _sendgrid_address(value):
    name, address = parseaddr(value)
    entry = {"email": address}
    if name:
        entry["name"] = name
    return entry


def send_via_sendgrid(recipient, subject, html_body, text_body, sender):
    config = current_app.config
    if config["MAIL_SUPPRESS_SEND"]:
        return None

    payload = {
        "personalizations": [{"to": [{"email": recipient}]}],
        "from": _sendgrid_address(sender),
        "subject": subject,
        # SendGrid requires text/plain before text/html.
        "content": [
            {"type": "text/plain", "value": text_body},
            {"type": "text/html", "value": html_body},
        ],
        # Transactional credentials mail: no click-tracking link rewriting,
        # so temporary-password and login links reach the user unaltered.
        "tracking_settings": {
            "click_tracking": {"enable": False, "enable_text": False},
            "open_tracking": {"enable": False},
        },
    }
    if config.get("SENDGRID_SANDBOX_MODE"):
        payload["mail_settings"] = {"sandbox_mode": {"enable": True}}

    request = urllib.request.Request(
        SENDGRID_SEND_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {config['SENDGRID_API_KEY']}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=SENDGRID_TIMEOUT_SECONDS) as response:
            return response.headers.get("X-Message-Id")
    except urllib.error.HTTPError as exc:
        # SendGrid's error body describes the problem (bad sender, bad key
        # scope, etc.) and never echoes the API key or message content.
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise EmailDeliveryError(f"SendGrid returned HTTP {exc.code}: {detail}") from exc


PROVIDERS = {
    "smtp": send_via_smtp,
    "sendgrid": send_via_sendgrid,
}


def deliver(recipient, subject, html_body, text_body):
    provider_name = current_app.config["EMAIL_PROVIDER"]
    provider = PROVIDERS.get(provider_name)
    if provider is None:
        raise EmailDeliveryError(f"Unknown EMAIL_PROVIDER: {provider_name!r}")
    return provider(
        recipient,
        subject,
        html_body,
        text_body,
        current_app.config["MAIL_DEFAULT_SENDER"],
    )
