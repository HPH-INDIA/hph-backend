import logging

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

from app.config import Config
from app.extensions import api, cors, db, mail, migrate
from app.responses import api_response

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    if app.config["MAIL_VALIDATE_CONFIG"]:
        from app.mailing.config import validate_mail_config

        validate_mail_config(app.config)

    if app.config["LOG_DECRYPTED_PAYLOADS"]:
        # Flask's logger otherwise sits at the root logger's default
        # (WARNING), which would silently swallow the .info() calls
        # encryption/hooks.py's _log_decrypted_payload makes.
        app.logger.setLevel(logging.INFO)

    db.init_app(app)
    migrate.init_app(app, db)
    api.init_app(app)
    mail.init_app(app)

    # Every process that creates this app — the web server AND the Celery
    # worker — needs its own configured Celery instance registered as
    # "current", or @shared_task-decorated calls (send_email_task.delay(),
    # etc.) resolve against an unconfigured default app instead of ours and
    # silently fail to reach the real broker. The web server only ever
    # produces (enqueues) tasks; the worker (celery_worker.py) reuses this
    # same instance to actually consume them.
    from app.celery_app import make_celery
    app.extensions["celery"] = make_celery(app)
    cors.init_app(
        app,
        resources={r"/api/*": {"origins": app.config["CORS_ALLOWED_ORIGINS"]}},
        supports_credentials=True,
        # §4b: custom response headers aren't visible to page JS via CORS
        # unless explicitly exposed — without this, the frontend's
        # response.headers.get("X-Encryption-Key-Version") silently returns
        # null in a real browser (fetch() from Node doesn't enforce this,
        # which is why it isn't caught by a Node-only test).
        expose_headers=["X-Encryption-Key-Version"],
    )

    # models must be imported before `flask db migrate` for autogenerate to see them
    from app.roles import models as roles_models  # noqa: F401
    from app.features import models as features_models  # noqa: F401
    from app.users import models as users_models  # noqa: F401
    from app.sessions import models as sessions_models  # noqa: F401
    from app.encryption import models as encryption_models  # noqa: F401
    from app.mailing import models as mailing_models  # noqa: F401
    from app.cohorts import models as cohorts_models  # noqa: F401
    from app.kairon import models as kairon_models  # noqa: F401
    from app.manual_daily_records import models as manual_daily_records_models  # noqa: F401
    from app.login_hours import models as login_hours_models  # noqa: F401
    from app.reports import models as reports_models  # noqa: F401

    from app.storage_imports import models as storage_import_models  # noqa: F401

    # blueprints (flask-smorest Blueprints; each declares its own url_prefix)
    from app.roles import bp as roles_bp
    from app.features import bp as features_bp
    from app.users import bp as users_bp
    from app.sessions import bp as sessions_bp
    from app.encryption import bp as encryption_bp
    from app.cohorts import bp as cohorts_bp
    from app.kairon import bp as kairon_bp
    from app.manual_daily_records import bp as manual_daily_records_bp
    from app.reports import bp as reports_bp
    from app.login_hours import bp as login_hours_bp
    api.register_blueprint(roles_bp)
    api.register_blueprint(features_bp)
    api.register_blueprint(users_bp)
    api.register_blueprint(sessions_bp)
    api.register_blueprint(encryption_bp)
    api.register_blueprint(cohorts_bp)
    api.register_blueprint(kairon_bp)
    api.register_blueprint(manual_daily_records_bp)
    api.register_blueprint(reports_bp)
    api.register_blueprint(login_hours_bp)
    from app.storage_imports import bp as storage_imports_bp
    api.register_blueprint(storage_imports_bp)

    # Appraisal system (appraisal_system/): directory API, gateway to its microservices and
    # Microsoft sign-in. Everything lives in that package; see appraisal_system/README.md.
    from appraisal_system.access_bff import init_access_bff

    init_access_bff(app, api)

    # Hook order is load-bearing: §4b's payload-decryption hook must run
    # BEFORE load_session, so the body is already plaintext by the time
    # load_session (and every view) reads it. before_request hooks run in
    # registration order, so decrypt_request_body is registered first.
    from app.encryption.hooks import decrypt_request_body, encrypt_response_body
    from app.sessions.hooks import load_session
    app.before_request(decrypt_request_body)
    app.before_request(load_session)
    app.after_request(encrypt_response_body)

    @app.get("/healthz")
    def healthcheck():
        return jsonify({"status": "ok"})

    @app.cli.command("rotate-encryption-key")
    def rotate_encryption_key_command():
        """Manually rotate the active payload-encryption key (§4b), e.g. on
        suspected compromise — independent of the 20-day schedule."""
        import click

        from app.encryption.services import rotate_key

        new_key = rotate_key(force=True)
        click.echo(f"Rotated to encryption key version {new_key.key_version}.")

    from app.cohorts.commands import import_legacy_data_command
    from app.mailing.commands import mail_smoke_test_command

    app.cli.add_command(import_legacy_data_command)
    app.cli.add_command(mail_smoke_test_command)
    from app.storage_imports.commands import process_file_imports, setup_import_storage
    app.cli.add_command(process_file_imports)
    app.cli.add_command(setup_import_storage)

    # Catches every error that raises via flask_smorest.abort() (our own
    # explicit aborts, and flask-smorest/webargs' own validation-error abort)
    # as well as plain Werkzeug errors that never touch our code (get_or_404(),
    # a 405 on a wrong verb) — so literally every response uses the same
    # {status, message, data} envelope.
    @app.errorhandler(HTTPException)
    def handle_http_exception(error):
        payload = getattr(error, "data", None) or {}

        if "messages" in payload:
            # raised by webargs/flask-smorest when @blp.arguments() fails to validate
            raw = payload["messages"]
            errors = raw.get("json", raw) if isinstance(raw, dict) else raw
            return api_response(data={"errors": errors}, message="Validation failed.", status=error.code)

        message = payload.get("message") or error.description or error.name
        extra = {k: v for k, v in payload.items() if k not in {"message", "data"}}
        return api_response(data=payload.get("data"), message=message, status=error.code, **extra)

    return app
