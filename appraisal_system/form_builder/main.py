"""
Form Builder main application
"""

# Disable ruff: E402-Imports not at the top of the file, I001-Import block is un-sorted
# ruff: noqa: E402, I001

import os

from common.hph_logging import logging
from common.configdb import database_url_from_env, get_sql_alchemy
from common.connexion_app import create_connexion_app
from config import ACCESS_SERVICE_URL, DB_SCHEMA, SERVICE_AUDIENCE


logger = logging.getLogger(__name__)
logging.getLogger("connexion").setLevel(logging.WARNING)

SPECIFICATION_DIR = os.path.dirname(os.path.abspath(__file__))

# Each service keeps its own Alembic history inside its own schema.
MIGRATE_OPTIONS = {"version_table_schema": DB_SCHEMA, "include_schemas": True}


def create_app(database_url=None, service_auth=None, directory_client=None):
    """Build the Connexion app; called once per process.

    Parameters
    ----------
    database_url : str, optional, default = None
        Overrides ``DATABASE_URL``; tests pass their own database.
    service_auth : common.service_auth.ServiceAuth, optional, default = None
        Overrides the ``SERVICE_AUTH_*`` environment configuration.
    directory_client : common.directory_client.DirectoryClient, optional, default = None
        Overrides the client built from ``ACCESS_SERVICE_URL``.

    Returns
    -------
    connexion.App
    """
    connex_app = create_connexion_app(
        __name__,
        specification_dir=SPECIFICATION_DIR,
        audience=SERVICE_AUDIENCE,
        database_url=database_url or database_url_from_env(),
        service_auth=service_auth,
    )
    app = connex_app.app

    from flask_migrate import Migrate

    import models  # noqa: F401 - registers the tables with SQLAlchemy metadata

    Migrate(app, get_sql_alchemy(), **MIGRATE_OPTIONS)

    app.extensions["directory_client"] = directory_client or _build_directory_client(app)

    connex_app.add_api(
        "form_builder.yaml",
        arguments={"title": "Form Builder Server"},
        pythonic_params=True,
        strict_validation=True,
    )

    return connex_app


def _build_directory_client(app):
    if not ACCESS_SERVICE_URL:
        logger.warning("ACCESS_SERVICE_URL is not set; project checks will fail")
        return None

    from common.directory_client import ACCESS_AUDIENCE, DirectoryClient
    from common.service_client import ServiceClient

    return DirectoryClient(
        ServiceClient(
            ACCESS_SERVICE_URL,
            ACCESS_AUDIENCE,
            app.extensions["service_auth"],
            unwrap_envelope=True,
        )
    )


_connex_app = None


def get_connex_app():
    global _connex_app

    if _connex_app is None:
        _connex_app = create_app()

    return _connex_app


def __getattr__(name):
    # Built on first access so `gunicorn main:app` and `flask --app main:app` share one app while
    # tests can call create_app() with their own configuration instead.
    if name == "app":
        return get_connex_app().app
    if name == "connex_app":
        return get_connex_app()

    raise AttributeError(name)


if __name__ == "__main__":
    get_connex_app().run(port=int(os.environ.get("PORT", "5101")))
