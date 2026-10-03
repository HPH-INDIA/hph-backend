"""Access/BFF side of the appraisal system, plugged into the existing monolith.

The monolith makes one call, ``init_access_bff(app, api)``, from ``app/__init__.py``. It adds:

- ``/api/directory/*``: read-only users and projects for the microservices (service tokens only).
- ``/api/appraisal/*`` and ``/api/form-builder/*``: the gateway to the microservices.
- ``/auth/microsoft/*``: Microsoft Entra ID sign-in, off until ``ENTRA_SIGN_IN_ENABLED`` is set.
"""


def init_access_bff(app, api):
    """Register the appraisal system's routes and settings on the monolith's Flask app.

    Parameters
    ----------
    app : flask.Flask
        The monolith app, after its own blueprints are registered.
    api : flask_smorest.Api
        The monolith's flask-smorest Api, so the directory appears in its OpenAPI docs.
    """
    from app.encryption import hooks as encryption_hooks
    from app.sessions import hooks as session_hooks
    from appraisal_system.access_bff.directory import bp as directory_bp
    from appraisal_system.access_bff.directory.paths import DIRECTORY_PATHS
    from appraisal_system.access_bff.entra import bp as entra_bp, init_entra
    from appraisal_system.access_bff.gateway import bp as gateway_bp
    from appraisal_system.access_bff.service_auth import init_service_auth
    from appraisal_system.access_bff.settings import load_settings

    load_settings(app)
    init_service_auth(app)
    init_entra(app)

    # The directory is called by services with a signed token, not by browsers: it needs neither
    # the session cookie nor the payload encryption the monolith applies to /api/*.
    session_hooks.EXEMPT_PATHS.update(DIRECTORY_PATHS)
    encryption_hooks.ENCRYPTION_EXEMPT_PATHS.update(DIRECTORY_PATHS)

    api.register_blueprint(directory_bp)
    app.register_blueprint(gateway_bp)
    app.register_blueprint(entra_bp)
