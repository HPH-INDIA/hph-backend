"""Shared SQLAlchemy instance for a service."""

import os

from flask_sqlalchemy import SQLAlchemy as _BaseSQLAlchemy


class SQLAlchemy(_BaseSQLAlchemy):
    """Flask-SQLAlchemy with ``pool_pre_ping`` so stale pooled connections are replaced."""

    def apply_pool_defaults(self, app, options):
        super().apply_pool_defaults(app, options)
        options["pool_pre_ping"] = True
        return options


_db = None


def set_sql_alchemy(app):
    global _db

    _db = SQLAlchemy(app)


def get_sql_alchemy():
    if _db is None:
        raise RuntimeError("SQLAlchemy instance is not set. Call set_sql_alchemy(app) first.")
    return _db


def database_url_from_env():
    """Return ``DATABASE_URL`` in the form SQLAlchemy 1.4 accepts.

    Returns
    -------
    str
        The URL with a ``postgres://`` scheme (as Supabase and Render print it) rewritten to
        ``postgresql://``.

    Raises
    ------
    RuntimeError
        If ``DATABASE_URL`` is not set.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set.")

    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]

    return url
