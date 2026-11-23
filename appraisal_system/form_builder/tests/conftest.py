# ruff: noqa: E402, I001

import os
import sys
from pathlib import Path

# Bare `pytest` does not always put the service root on sys.path; models, services, handlers,
# config and the `common` symlink all live there.
_service_root = Path(__file__).resolve().parents[1]
if str(_service_root) not in sys.path:
    sys.path.insert(0, str(_service_root))

import pytest
from sqlalchemy import event, text

from common.configdb import get_sql_alchemy
from common.directory_client import DirectoryProject
from common.service_auth import (
    ServiceAuth,
    generate_key_pair,
    load_private_key,
    parse_trusted_issuers,
)
from config_dbname import DB_SCHEMA


# SQLite in memory by default; set TEST_DATABASE_URL to run against Postgres.
TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "sqlite:///:memory:")
AUDIENCE = "hph-form-builder"

_bff_private, _bff_public = generate_key_pair()
_appraisal_private, _appraisal_public = generate_key_pair()
_self_private, _ = generate_key_pair()


class FakeDirectoryClient:
    """Stands in for the Access directory API."""

    def __init__(self):
        self.projects_by_id = {1: DirectoryProject(1, "CODING"), 2: DirectoryProject(2, "RCM")}
        self.users_by_id = {}

    def projects(self, ids=None):
        if ids is None:
            return list(self.projects_by_id.values())
        return [self.projects_by_id[pid] for pid in ids if pid in self.projects_by_id]

    def users(self, ids=None, project_id=None, role_types=None, active=None):
        users = list(self.users_by_id.values())
        if ids is not None:
            users = [user for user in users if user.id in set(ids)]
        if project_id is not None:
            users = [user for user in users if user.project_id == project_id]
        if role_types:
            users = [user for user in users if user.role_type_code in set(role_types)]
        if active is not None:
            users = [user for user in users if user.is_active == active]
        return users


class Tokens:
    """Mints the headers a request needs, as the BFF or another service would send them."""

    def __init__(self):
        self.bff = ServiceAuth("hph-bff", load_private_key(_bff_private), None, {})
        self.appraisal = ServiceAuth(
            "hph-appraisal", load_private_key(_appraisal_private), None, {}
        )
        stranger_private, _ = generate_key_pair()
        self.stranger = ServiceAuth("hph-stranger", load_private_key(stranger_private), None, {})

    def user(
        self,
        user_id=1,
        permissions=("form_builder:read", "form_builder:write"),
        role_type="admin",
        project_id=None,
        audience=AUDIENCE,
    ):
        token = self.bff.mint_user_token(audience, user_id, role_type, project_id, permissions)
        return {"Authorization": f"Bearer {token}", "caller_id": str(user_id)}

    def admin(self):
        return self.user()

    def reader(self, user_id=2):
        return self.user(user_id=user_id, permissions=("form_builder:read",))

    def nobody(self, user_id=3):
        return self.user(user_id=user_id, permissions=(), role_type="employee", project_id=1)

    def appraisal_service(self, audience=AUDIENCE):
        token = self.appraisal.mint_service_token(audience)
        return {"Authorization": f"Bearer {token}", "caller_id": "-1"}

    def stranger_service(self):
        token = self.stranger.mint_service_token(AUDIENCE)
        return {"Authorization": f"Bearer {token}", "caller_id": "-1"}


def _service_auth():
    trusted = parse_trusted_issuers(
        {
            "hph-bff": {"publicKey": _bff_public, "tokenTypes": ["user"]},
            "hph-appraisal": {"publicKey": _appraisal_public, "tokenTypes": ["service"]},
        }
    )
    return ServiceAuth("hph-form-builder", load_private_key(_self_private), AUDIENCE, trusted)


def _configure_app():
    import main

    directory = FakeDirectoryClient()
    connex_app = main.create_app(
        database_url=TEST_DB_URL, service_auth=_service_auth(), directory_client=directory
    )
    app = connex_app.app

    with app.app_context():
        engine = get_sql_alchemy().get_engine()
        if engine.dialect.name == "sqlite":
            # SQLite has no schemas; an attached database named like the schema stands in.
            @event.listens_for(engine, "connect")
            def _attach_schema(dbapi_connection, connection_record):
                dbapi_connection.execute(f"ATTACH DATABASE ':memory:' AS {DB_SCHEMA}")

        else:
            with engine.begin() as connection:
                connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{DB_SCHEMA}"'))

    return app, directory


app, directory_client = _configure_app()
db = get_sql_alchemy()


@pytest.fixture(autouse=True)
def recreate_tables():
    with app.app_context():
        db.session.remove()
        db.drop_all()
        db.create_all()
        directory_client.__init__()
        yield
        db.session.remove()


@pytest.fixture()
def client():
    return app.test_client()


@pytest.fixture()
def tokens():
    return Tokens()


@pytest.fixture()
def directory():
    return directory_client


@pytest.fixture()
def flask_app():
    return app
