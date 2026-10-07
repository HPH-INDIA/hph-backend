"""Settings for the Access/BFF plugin, read from the environment into ``app.config``.

Kept here rather than in the monolith's ``app/config.py`` so the plugin is self-contained.
Values already present in ``app.config`` win, which lets tests override them.
"""

import os
from urllib.parse import urlsplit


def env_flag(name, default=False):
    """Read a boolean environment variable; reject ambiguous values."""
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default

    value = raw_value.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False

    raise ValueError(f"{name} must be one of: true, false, 1, 0, yes, no, on, off")


def load_settings(app):
    defaults = {
        # Private URLs of the microservices the gateway forwards to.
        "APPRAISAL_SERVICE_URL": os.environ.get("APPRAISAL_SERVICE_URL", ""),
        "FORM_BUILDER_SERVICE_URL": os.environ.get("FORM_BUILDER_SERVICE_URL", ""),
        "SERVICE_GATEWAY_TIMEOUT_SECONDS": float(
            os.environ.get("SERVICE_GATEWAY_TIMEOUT_SECONDS", "15")
        ),
        # Microsoft Entra ID sign-in (off until switched on), see docs/MICROSOFT_SIGN_IN.md.
        "ENTRA_SIGN_IN_ENABLED": env_flag("ENTRA_SIGN_IN_ENABLED"),
        "ENTRA_TENANT_ID": os.environ.get("ENTRA_TENANT_ID", ""),
        "ENTRA_CLIENT_ID": os.environ.get("ENTRA_CLIENT_ID", ""),
        "ENTRA_CLIENT_SECRET": os.environ.get("ENTRA_CLIENT_SECRET", ""),
        "ENTRA_CLIENT_CERTIFICATE_PRIVATE_KEY": os.environ.get(
            "ENTRA_CLIENT_CERTIFICATE_PRIVATE_KEY", ""
        ),
        "ENTRA_CLIENT_CERTIFICATE_THUMBPRINT": os.environ.get(
            "ENTRA_CLIENT_CERTIFICATE_THUMBPRINT", ""
        ),
        "ENTRA_REDIRECT_URI": os.environ.get("ENTRA_REDIRECT_URI", ""),
        # First Microsoft sign-in links to the HPH user with the same email (admins excepted).
        "ENTRA_LINK_BY_EMAIL": env_flag("ENTRA_LINK_BY_EMAIL", True),
        # Switch off once everyone signs in with Microsoft.
        "PASSWORD_LOGIN_ENABLED": env_flag("PASSWORD_LOGIN_ENABLED", True),
        # Where the browser lands after Microsoft sign-in; defaults to the login page's origin.
        "FRONTEND_APP_URL": os.environ.get("FRONTEND_APP_URL")
        or _origin(app.config.get("FRONTEND_LOGIN_URL", "")),
    }
    for key, value in defaults.items():
        app.config.setdefault(key, value)


def _origin(url):
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""
