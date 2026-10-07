"""Microsoft Entra ID sign-in for the HPH monolith (FR-19 to FR-22), off until
``ENTRA_SIGN_IN_ENABLED`` is set.

Sign-in ends in the monolith's normal session cookie, so the rest of the app and the gateway
work unchanged. ``PASSWORD_LOGIN_ENABLED=false`` turns password sign-in off once everyone has
moved to Microsoft.
"""

from flask import Blueprint, current_app, request
from flask_smorest import abort


bp = Blueprint("entra", __name__, url_prefix="/auth/microsoft")

PASSWORD_LOGIN_PATH = ("/api/sessions/login", "POST")


def init_entra(app):
    """Validate the settings, create the Microsoft client and register commands and checks."""
    from appraisal_system.access_bff.entra import models  # noqa: F401 - for Alembic autogenerate
    from appraisal_system.access_bff.entra.client import EntraClient
    from appraisal_system.access_bff.entra.commands import register_commands
    from appraisal_system.access_bff.entra.services import client_credential, validate_settings

    if app.config["ENTRA_SIGN_IN_ENABLED"]:
        validate_settings(app.config)

    app.extensions["entra_client"] = EntraClient(
        tenant_id=app.config["ENTRA_TENANT_ID"],
        client_id=app.config["ENTRA_CLIENT_ID"],
        client_credential=client_credential(app.config),
        redirect_uri=app.config["ENTRA_REDIRECT_URI"],
    )
    register_commands(app)
    app.before_request(block_password_login_when_disabled)


def block_password_login_when_disabled():
    if (request.path, request.method) != PASSWORD_LOGIN_PATH:
        return None
    if current_app.config["PASSWORD_LOGIN_ENABLED"]:
        return None

    abort(
        403,
        message="Password sign-in is turned off. Use Sign in with Microsoft.",
        code="password_login_disabled",
    )


from appraisal_system.access_bff.entra import routes  # noqa: E402,F401
