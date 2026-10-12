# ruff: noqa: E402, I001

import copy
import os
import sys
from datetime import date
from pathlib import Path

# Bare `pytest` does not always put the service root on sys.path; models, services, handlers,
# config and the `common` symlink all live there.
_service_root = Path(__file__).resolve().parents[1]
if str(_service_root) not in sys.path:
    sys.path.insert(0, str(_service_root))

import pytest
from sqlalchemy import event, text

from common.configdb import get_sql_alchemy
from common.directory_client import DirectoryProject, DirectoryUser
from common.service_auth import (
    ServiceAuth,
    generate_key_pair,
    load_private_key,
    parse_trusted_issuers,
)
from config_dbname import DB_SCHEMA


# SQLite in memory by default; set TEST_DATABASE_URL to run against Postgres.
TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "sqlite:///:memory:")
AUDIENCE = "hph-appraisal"

BFF_KEYS = generate_key_pair()
APPRAISAL_KEYS = generate_key_pair()

ADMIN_ID = 1
MANAGER_ID = 10
LEAD_ID = 20
ORPHAN_LEAD_ID = 21
EMPLOYEE_ID = 30
SECOND_EMPLOYEE_ID = 31
ORPHAN_EMPLOYEE_ID = 32
INACTIVE_EMPLOYEE_ID = 33
DEPARTED_EMPLOYEE_ID = 34
OTHER_MANAGER_ID = 11
OTHER_LEAD_ID = 22
RCM_EMPLOYEE_ID = 40
RCM_LEAD_ID = 41
RCM_MANAGER_ID = 42

CODING_PROJECT = 1
RCM_PROJECT = 2

EMPLOYEE_FORM_VERSION = 101
LEAD_FORM_VERSION = 102

PERMISSIONS = {
    "admin": ("appraisal_cycle_admin:read", "appraisal_cycle_admin:write"),
    "manager": ("appraisal_review:read", "appraisal_review:write"),
    "lead": (
        "appraisal_review:read",
        "appraisal_review:write",
        "appraisal_self:read",
        "appraisal_self:write",
    ),
    "employee": ("appraisal_self:read", "appraisal_self:write"),
}

EMPLOYEE_DEFINITION = {
    "sections": [
        {
            "key": "self_assessment",
            "title": "Self-assessment",
            "filled_by": "employee",
            "fields": [
                {
                    "key": "achievements",
                    "type": "long_text",
                    "label": "Achievements",
                    "required": True,
                },
                {
                    "key": "self_rating",
                    "type": "rating",
                    "label": "Self rating",
                    "required": True,
                    "scale": {"min": 1, "max": 5},
                },
                {"key": "notes", "type": "long_text", "label": "Notes", "required": False},
            ],
        },
        {
            "key": "lead_assessment",
            "title": "Lead review",
            "filled_by": "reviewer",
            "review_stage": "lead",
            "fields": [
                {
                    "key": "lead_comments",
                    "type": "long_text",
                    "label": "Lead comments",
                    "required": True,
                }
            ],
        },
        {
            "key": "manager_assessment",
            "title": "Manager review",
            "filled_by": "reviewer",
            "review_stage": "manager",
            "fields": [
                {
                    "key": "manager_rating",
                    "type": "rating",
                    "label": "Final rating",
                    "required": True,
                    "scale": {"min": 1, "max": 5},
                }
            ],
        },
    ]
}

LEAD_DEFINITION = {
    "sections": [
        EMPLOYEE_DEFINITION["sections"][0],
        EMPLOYEE_DEFINITION["sections"][2],
    ]
}


def directory_user(user_id, role_type, project_id=CODING_PROJECT, reports_to_id=None, **extra):
    values = {
        "id": user_id,
        "emp_id": f"E{user_id}",
        "first_name": role_type.title(),
        "last_name": str(user_id),
        "email": f"user{user_id}@example.com",
        "role_type_code": role_type,
        "role_title": role_type.title(),
        "project_id": project_id,
        "reports_to_id": reports_to_id,
        "is_active": True,
        "last_working_day": None,
    }
    values.update(extra)
    return DirectoryUser(**values)


def default_users():
    return [
        directory_user(ADMIN_ID, "admin", project_id=None),
        directory_user(MANAGER_ID, "manager"),
        directory_user(OTHER_MANAGER_ID, "manager"),
        directory_user(LEAD_ID, "lead", reports_to_id=MANAGER_ID),
        directory_user(ORPHAN_LEAD_ID, "lead", reports_to_id=None),
        directory_user(OTHER_LEAD_ID, "lead", reports_to_id=OTHER_MANAGER_ID),
        directory_user(EMPLOYEE_ID, "employee", reports_to_id=LEAD_ID),
        directory_user(SECOND_EMPLOYEE_ID, "employee", reports_to_id=LEAD_ID),
        directory_user(ORPHAN_EMPLOYEE_ID, "employee", reports_to_id=None),
        directory_user(INACTIVE_EMPLOYEE_ID, "employee", reports_to_id=LEAD_ID, is_active=False),
        directory_user(
            DEPARTED_EMPLOYEE_ID,
            "employee",
            reports_to_id=LEAD_ID,
            last_working_day=date(2025, 12, 31),
        ),
        directory_user(RCM_MANAGER_ID, "manager", project_id=RCM_PROJECT),
        directory_user(RCM_LEAD_ID, "lead", project_id=RCM_PROJECT, reports_to_id=RCM_MANAGER_ID),
        directory_user(
            RCM_EMPLOYEE_ID, "employee", project_id=RCM_PROJECT, reports_to_id=RCM_LEAD_ID
        ),
    ]


class FakeDirectoryClient:
    """Stands in for the Access directory API with a small org."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.users_by_id = {user.id: user for user in default_users()}
        self.projects_by_id = {
            CODING_PROJECT: DirectoryProject(CODING_PROJECT, "CODING"),
            RCM_PROJECT: DirectoryProject(RCM_PROJECT, "RCM"),
        }
        self.fail = False

    def add(self, user):
        self.users_by_id[user.id] = user

    def users(self, ids=None, project_id=None, role_types=None, active=None):
        if self.fail:
            import common.errors as errors

            raise errors.Unavailable("hph-access is unavailable")

        users = list(self.users_by_id.values())
        if ids is not None:
            wanted = set(ids)
            users = [user for user in users if user.id in wanted]
        if project_id is not None:
            users = [user for user in users if user.project_id == project_id]
        if role_types:
            users = [user for user in users if user.role_type_code in set(role_types)]
        if active is not None:
            users = [user for user in users if user.is_active == active]
        return users

    def projects(self, ids=None):
        if ids is None:
            return list(self.projects_by_id.values())
        return [self.projects_by_id[pid] for pid in ids if pid in self.projects_by_id]


class FakeFormsClient:
    """Stands in for the Form Builder: resolves forms and applies its basic answer rules."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.forms = {
            (CODING_PROJECT, "employee"): (
                EMPLOYEE_FORM_VERSION,
                "Coding employee",
                EMPLOYEE_DEFINITION,
            ),
            (CODING_PROJECT, "lead"): (LEAD_FORM_VERSION, "Coding lead", LEAD_DEFINITION),
        }
        self.validations = []

    def _definition(self, version_id):
        for found_version, _, definition in self.forms.values():
            if found_version == version_id:
                return definition
        raise AssertionError(f"unknown version {version_id}")

    def resolve(self, project_id, role_type):
        entry = self.forms.get((project_id, role_type))
        if entry is None:
            return None
        version_id, name, definition = entry
        return {
            "form_id": version_id * 10,
            "form_name": name,
            "version_id": version_id,
            "version_no": 1,
            "definition": copy.deepcopy(definition),
        }

    def validate(self, version_id, answers, stage, mode):
        self.validations.append((version_id, stage, mode))
        fields = {}
        for section in self._definition(version_id)["sections"]:
            section_stage = (
                "employee" if section["filled_by"] == "employee" else section["review_stage"]
            )
            if section_stage == stage:
                fields.update({field["key"]: field for field in section["fields"]})

        errors = [
            {"path": key, "message": "is not a field of this stage"}
            for key in answers
            if key not in fields
        ]
        normalised = {
            key: value.strip() or None if isinstance(value, str) else value
            for key, value in answers.items()
            if key in fields
        }
        if mode == "submit":
            errors += [
                {"path": key, "message": "is required"}
                for key, field in fields.items()
                if field.get("required") and normalised.get(key) in (None, "", [])
            ]
        return normalised, errors


class Tokens:
    """Mints the headers the BFF would send for each person in the fake org."""

    def __init__(self):
        self.bff = ServiceAuth("hph-bff", load_private_key(BFF_KEYS[0]), None, {})
        self.appraisal = ServiceAuth("hph-appraisal", load_private_key(APPRAISAL_KEYS[0]), None, {})

    def user(self, user_id, role_type, permissions=None, project_id=CODING_PROJECT):
        permissions = PERMISSIONS.get(role_type, ()) if permissions is None else permissions
        token = self.bff.mint_user_token(AUDIENCE, user_id, role_type, project_id, permissions)
        return {"Authorization": f"Bearer {token}", "caller_id": str(user_id)}

    def admin(self):
        return self.user(ADMIN_ID, "admin", project_id=None)

    def manager(self, user_id=MANAGER_ID):
        return self.user(user_id, "manager")

    def lead(self, user_id=LEAD_ID):
        return self.user(user_id, "lead")

    def employee(self, user_id=EMPLOYEE_ID):
        return self.user(user_id, "employee")

    def service(self):
        token = self.appraisal.mint_service_token(AUDIENCE)
        return {"Authorization": f"Bearer {token}", "caller_id": "-1"}


def _service_auth():
    trusted = parse_trusted_issuers(
        {
            "hph-bff": {"publicKey": BFF_KEYS[1], "tokenTypes": ["user"]},
            "hph-appraisal": {"publicKey": APPRAISAL_KEYS[1], "tokenTypes": ["service"]},
        }
    )
    return ServiceAuth("hph-appraisal", load_private_key(APPRAISAL_KEYS[0]), AUDIENCE, trusted)


def _configure_app():
    import main

    directory = FakeDirectoryClient()
    forms = FakeFormsClient()
    connex_app = main.create_app(
        database_url=TEST_DB_URL,
        service_auth=_service_auth(),
        directory_client=directory,
        forms_client=forms,
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

    return app, directory, forms


app, directory_client, forms_client = _configure_app()
db = get_sql_alchemy()


@pytest.fixture(autouse=True)
def recreate_tables():
    with app.app_context():
        db.session.remove()
        db.drop_all()
        db.create_all()
        directory_client.reset()
        forms_client.reset()
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
def forms():
    return forms_client


@pytest.fixture()
def flask_app():
    return app
