import pytest

from app.mailing.config import validate_mail_config


def _valid_config(**overrides):
    config = {
        "MAIL_SERVER": "smtp.example.com",
        "MAIL_PORT": 587,
        "MAIL_USE_TLS": True,
        "MAIL_USE_SSL": False,
        "MAIL_USERNAME": "smtp-user",
        "MAIL_PASSWORD": "smtp-password",
        "MAIL_DEFAULT_SENDER": "noreply@example.com",
        "FRONTEND_LOGIN_URL": "https://testing.example.com/login",
    }
    config.update(overrides)
    return config


def test_validate_mail_config_accepts_testing_smtp_settings():
    validate_mail_config(_valid_config())


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"MAIL_SERVER": "localhost"}, "must not point to Mailpit/localhost"),
        ({"MAIL_USE_SSL": True}, "cannot both be enabled"),
        ({"MAIL_PASSWORD": None}, "must be set together"),
        ({"MAIL_DEFAULT_SENDER": "not-an-email"}, "must be a valid email address"),
        (
            {"MAIL_DEFAULT_SENDER": "noreply@your-domain.example"},
            "must not use a reserved placeholder domain",
        ),
        ({"FRONTEND_LOGIN_URL": "http://testing.example.com/login"}, "absolute HTTPS URL"),
    ],
)
def test_validate_mail_config_rejects_unsafe_deployment_settings(overrides, expected):
    with pytest.raises(RuntimeError, match=expected):
        validate_mail_config(_valid_config(**overrides))


def test_send_email_task_sends_and_records_success(monkeypatch, app):
    from app.mailing import providers, tasks

    sent_messages = []
    log_calls = []
    monkeypatch.setitem(app.config, "EMAIL_PROVIDER", "smtp")
    monkeypatch.setattr(providers.mail, "send", sent_messages.append)
    monkeypatch.setattr(tasks, "_log", lambda *args: log_calls.append(args))

    with app.app_context():
        tasks.send_email_task.run(
            "person@example.com",
            "Subject",
            "<p>Body</p>",
            "Body",
            "test_template",
        )

    assert len(sent_messages) == 1
    message = sent_messages[0]
    assert message.recipients == ["person@example.com"]
    assert message.sender == app.config["MAIL_DEFAULT_SENDER"]
    assert log_calls == [
        ("person@example.com", "test_template", "sent", message.msgId, None)
    ]


def _sendgrid_config(**overrides):
    settings = {
        "EMAIL_PROVIDER": "sendgrid",
        "SENDGRID_API_KEY": "SG.test-key",
        "MAIL_SERVER": "localhost",
        "MAIL_USERNAME": None,
        "MAIL_PASSWORD": None,
    }
    settings.update(overrides)
    return _valid_config(**settings)


def test_validate_mail_config_accepts_sendgrid_without_smtp_settings():
    validate_mail_config(_sendgrid_config())


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"SENDGRID_API_KEY": None}, "SENDGRID_API_KEY is required"),
        ({"SENDGRID_API_KEY": "not-a-key"}, "does not look like a SendGrid API key"),
        ({"EMAIL_PROVIDER": "pigeon"}, "must be 'smtp' or 'sendgrid'"),
    ],
)
def test_validate_mail_config_rejects_bad_sendgrid_settings(overrides, expected):
    with pytest.raises(RuntimeError, match=expected):
        validate_mail_config(_sendgrid_config(**overrides))


class _FakeResponse:
    headers = {"X-Message-Id": "sg-message-123"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _enable_sendgrid(monkeypatch, app):
    monkeypatch.setitem(app.config, "EMAIL_PROVIDER", "sendgrid")
    monkeypatch.setitem(app.config, "SENDGRID_API_KEY", "SG.test-key")
    monkeypatch.setitem(app.config, "SENDGRID_SANDBOX_MODE", False)
    monkeypatch.setitem(app.config, "MAIL_SUPPRESS_SEND", False)
    monkeypatch.setitem(app.config, "MAIL_DEFAULT_SENDER", "HPH <noreply@hph.example.com>")


def test_send_email_task_delivers_through_sendgrid(monkeypatch, app):
    import json

    from app.mailing import providers, tasks

    _enable_sendgrid(monkeypatch, app)
    requests = []
    log_calls = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return _FakeResponse()

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(tasks, "_log", lambda *args: log_calls.append(args))

    with app.app_context():
        tasks.send_email_task.run(
            "person@example.com", "Subject", "<p>Body</p>", "Body", "test_template"
        )

    assert len(requests) == 1
    request, timeout = requests[0]
    assert request.full_url == providers.SENDGRID_SEND_URL
    assert request.get_header("Authorization") == "Bearer SG.test-key"
    payload = json.loads(request.data)
    assert payload["personalizations"] == [{"to": [{"email": "person@example.com"}]}]
    assert payload["from"] == {"email": "noreply@hph.example.com", "name": "HPH"}
    assert [part["type"] for part in payload["content"]] == ["text/plain", "text/html"]
    assert payload["tracking_settings"]["click_tracking"]["enable"] is False
    assert "mail_settings" not in payload
    assert log_calls == [
        ("person@example.com", "test_template", "sent", "sg-message-123", None)
    ]


def test_sendgrid_http_error_is_logged_and_retried(monkeypatch, app):
    import io
    import urllib.error

    from app.mailing import providers, tasks

    _enable_sendgrid(monkeypatch, app)
    log_calls = []

    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 403, "Forbidden", {}, io.BytesIO(b'{"errors":[{"message":"bad sender"}]}')
        )

    class Retry(Exception):
        pass

    def fake_retry(exc):
        raise Retry(exc)

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(tasks, "_log", lambda *args: log_calls.append(args))
    monkeypatch.setattr(tasks.send_email_task, "retry", fake_retry)

    with app.app_context(), pytest.raises(Retry):
        tasks.send_email_task.run(
            "person@example.com", "Subject", "<p>Body</p>", "Body", "test_template"
        )

    assert len(log_calls) == 1
    recipient, template, status, message_id, error = log_calls[0]
    assert (recipient, template, status, message_id) == (
        "person@example.com", "test_template", "failed", None
    )
    assert "HTTP 403" in error and "bad sender" in error
    assert "SG.test-key" not in error


def test_sendgrid_respects_suppress_send(monkeypatch, app):
    from app.mailing import providers

    _enable_sendgrid(monkeypatch, app)
    monkeypatch.setitem(app.config, "MAIL_SUPPRESS_SEND", True)
    monkeypatch.setattr(
        providers.urllib.request, "urlopen", lambda *a, **k: pytest.fail("should not send")
    )

    with app.app_context():
        assert providers.deliver("person@example.com", "S", "<p>B</p>", "B") is None
