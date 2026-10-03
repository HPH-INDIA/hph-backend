import os

# Must happen before `app.config` (and therefore `app`) is ever imported -
# Config.SQLALCHEMY_DATABASE_URI is read from os.environ at class-body
# (import) time, not lazily. load_dotenv() (called by app.config) defaults
# to override=False, so this wins over whatever DATABASE_URL is in .env,
# while every other .env value (SECRET_KEY, ENCRYPTION_MASTER_KEY, ...)
# still loads normally.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg2://localhost:5432/vitalyse_health_test"
)
# Unit/integration tests provide their own provider configuration and must not
# inherit deployment-only fail-fast validation from a developer's .env file.
os.environ["EMAIL_VALIDATE_CONFIG"] = "false"

import datetime as dt
import json

import psycopg2
import pytest
from flask_migrate import upgrade as migrate_upgrade
from sqlalchemy import text

from app import create_app
from app.cohorts.models import StageTargetRule
from app.encryption.crypto import decrypt_payload, encrypt_payload
from app.encryption.passwords import hash_password
from app.encryption.services import get_active_key, get_key_for_decryption
from app.extensions import db
from app.roles.models import Role, RoleType
from app.users.models import Project, User

# Tables this feature's tests actually write to, in FK-safe delete order.
# role_types/roles/users/features/stages/encryption_keys come from the real
# seed migrations (via migrate_upgrade() below) and are never touched by a
# test, so they're set up once per session rather than per test.
_COHORT_MODULE_TABLES_FK_ORDER = (
    "storage_import_slots",
    "storage_imports",
    "login_hour_records",
    "login_hours_upload_batches",
    "user_stage_periods",
    "cohort_memberships",
    "stage_exceptions",
    "cohort_join_reviews",
    "coder_stage_periods",
    "kairon_completions",
    "manual_production_facts",
    # app/kairon's own tables - reference kairon_chart_records/
    # kairon_upload_batches (and users, which is never cleaned here), not
    # coders, so only their order relative to each other matters.
    "kairon_chart_history",
    "kairon_chart_analyst_actions",
    "kairon_import_chunks",
    "kairon_chart_records",
    "kairon_upload_batches",
    # app/manual_daily_records' own table - references users (never
    # cleaned here) only, so its position relative to the rest is arbitrary.
    "manual_daily_records",
    "coder_aliases",
    "coders",
    "cohorts",
)

_SEED_STAGE_TARGETS = {"M1": 7, "M2": 14, "M3": 20, "M4": 30, "Steady State": 30}


def _ensure_test_database_exists():
    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg2://", "postgresql://")
    db_name = url.rsplit("/", 1)[1]
    admin_url = url.rsplit("/", 1)[0] + "/postgres"
    conn = psycopg2.connect(admin_url)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            if cur.fetchone() is None:
                cur.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        conn.close()


@pytest.fixture(scope="session")
def app():
    _ensure_test_database_exists()
    flask_app = create_app()
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        migrate_upgrade()  # real Alembic chain - same schema and seed data as dev/prod
    return flask_app


@pytest.fixture(autouse=True)
def _clean_cohorts_tables(app):
    """Runs each test inside an app context, then deletes every row this
    feature's tests could have written and restores stage_target_rules to
    its seeded state - simpler and more robust than trying to wrap
    service-layer code (which calls db.session.commit() itself, e.g. every
    compute_stage_periods() call) in a rolled-back outer transaction.
    """
    with app.app_context():
        yield
        for table in _COHORT_MODULE_TABLES_FK_ORDER:
            db.session.execute(text(f"DELETE FROM {table}"))
        from app.storage_imports.models import StorageImportSlot
        db.session.add_all([StorageImportSlot(kind="kairon"), StorageImportSlot(kind="manual")])
        db.session.execute(text("DELETE FROM stage_target_rules"))
        superadmin = User.query.join(Role).join(RoleType).filter(RoleType.code == "super_admin").first()
        for stage_code, target in _SEED_STAGE_TARGETS.items():
            db.session.add(
                StageTargetRule(
                    stage_code=stage_code,
                    effective_from=dt.date(1900, 1, 1),
                    effective_to=None,
                    daily_target=target,
                    created_by_id=superadmin.id,
                )
            )
        db.session.commit()
        db.session.remove()


@pytest.fixture
def superadmin(app):
    role_type = RoleType.query.filter_by(code="super_admin").one()
    role = Role.query.filter_by(role_type_id=role_type.id).first()
    return User.query.filter_by(role_id=role.id).first()


@pytest.fixture
def employee_user(app):
    """A non-superadmin user, for asserting a write endpoint's role gate
    actually rejects someone below super_admin. Get-or-create by a fixed
    test email so re-runs stay idempotent (users isn't in the per-test
    cleanup list - there's no reason to churn it every test).
    """
    role_type = RoleType.query.filter_by(code="employee").one()
    role = Role.query.filter_by(role_type_id=role_type.id).first()
    project = Project.query.filter_by(name="CODING").one()
    user = User.query.filter_by(email="test-employee@example.com").first()
    if user is None:
        user = User(
            email="test-employee@example.com",
            first_name="Test",
            last_name="Employee",
            emp_id="TEST-EMPLOYEE",
            role_id=role.id,
            project_id=project.id,
            password_hash=hash_password("test-password"),
            first_login=False,
            is_active=True,
        )
        db.session.add(user)
        db.session.commit()
    elif user.project_id != project.id:
        user.project_id = project.id
        db.session.commit()
    return user


@pytest.fixture
def manager_user(app):
    """A Manager-role user - the only starter role granted the
    kairon_management feature (see the seed-kairon-management-feature
    migration) - for asserting the kairon write endpoints accept exactly
    the role the product requirement names. Get-or-create, same rationale
    as employee_user above.
    """
    role_type = RoleType.query.filter_by(code="manager").one()
    role = Role.query.filter_by(role_type_id=role_type.id).first()
    project = Project.query.filter_by(name="CODING").one()
    user = User.query.filter_by(email="test-manager@example.com").first()
    if user is None:
        user = User(
            email="test-manager@example.com",
            first_name="Test",
            last_name="Manager",
            emp_id="TEST-MANAGER",
            role_id=role.id,
            project_id=project.id,
            password_hash=hash_password("test-password"),
            first_login=False,
            is_active=True,
        )
        db.session.add(user)
        db.session.commit()
    elif user.project_id != project.id:
        user.project_id = project.id
        db.session.commit()
    return user


@pytest.fixture
def lead_user(app, employee_user, manager_user):
    role_type = RoleType.query.filter_by(code="lead").one()
    role = Role.query.filter_by(role_type_id=role_type.id).first()
    user = User.query.filter_by(email="test-lead@example.com").first()
    if user is None:
        user = User(
            email="test-lead@example.com",
            first_name="Test",
            last_name="Lead",
            emp_id="TEST-LEAD",
            role_id=role.id,
            project_id=manager_user.project_id,
            reports_to_id=manager_user.id,
            password_hash=hash_password("test-password"),
            first_login=False,
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
    previous_lead_id = employee_user.reports_to_id
    employee_user.reports_to_id = user.id
    db.session.commit()
    yield user
    employee_user.reports_to_id = previous_lead_id
    db.session.commit()


class ApiClient:
    """Wraps the Flask test client with this app's payload encryption
    (§4b) - every request/response body is AES-GCM encrypted end to end
    (see app/encryption/hooks.py), so a plain test_client.post() with a
    plaintext JSON body would just get rejected as undecryptable.
    """

    def __init__(self, test_client):
        self.client = test_client

    def _decrypt(self, response):
        key_version = int(response.headers["X-Encryption-Key-Version"])
        key = get_key_for_decryption(key_version)
        return json.loads(decrypt_payload(response.data, key))

    def get(self, path):
        response = self.client.get(path)
        return response.status_code, self._decrypt(response)

    def delete(self, path, body=None):
        if body is None:
            response = self.client.delete(path)
        else:
            version, key = get_active_key()
            encrypted = encrypt_payload(json.dumps(body).encode(), key)
            response = self.client.delete(
                path,
                data=encrypted,
                content_type="application/json",
                headers={"X-Encryption-Key-Version": str(version)},
            )
        return response.status_code, self._decrypt(response)

    def _write(self, method, path, body):
        # The Content-Type a real client sends is still application/json -
        # only the bytes underneath are ciphertext, decrypted back into
        # plain JSON by decrypt_request_body() before any JSON parsing runs.
        # application/octet-stream is only ever the *response's* mimetype
        # (see encrypt_response_body) - it's never what the request itself
        # is labeled as.
        version, key = get_active_key()
        encrypted = encrypt_payload(json.dumps(body or {}).encode(), key)
        response = getattr(self.client, method)(
            path,
            data=encrypted,
            headers={
                "X-Encryption-Key-Version": str(version),
                "Content-Type": "application/json",
            },
        )
        return response.status_code, self._decrypt(response)

    def post(self, path, body=None):
        return self._write("post", path, body)

    def patch(self, path, body=None):
        return self._write("patch", path, body)

    def login(self, email, password):
        return self.post("/api/sessions/login", {"email": email, "password": password})


@pytest.fixture
def api_client(app):
    return ApiClient(app.test_client())
