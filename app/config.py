import os

from dotenv import load_dotenv
from sqlalchemy.engine import URL

load_dotenv()


def _database_uri():
    """Use DATABASE_URL locally or Cloud SQL's Unix socket in Cloud Run."""
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        return database_url

    return URL.create(
        drivername="postgresql+psycopg2",
        username=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"],
        query={"host": os.environ["INSTANCE_UNIX_SOCKET"]},
    )


def _env_bool(name, default=False):
    """Read a boolean environment variable and reject ambiguous values."""
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default

    value = raw_value.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be one of: true, false, 1, 0, yes, no, on, off")


class Config:
    SQLALCHEMY_DATABASE_URI = _database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 1800,
        "pool_size": 5,
        "max_overflow": 2,
    }
    SECRET_KEY = os.environ["SECRET_KEY"]
    # Stable keyed hash used to identify Kairon charts without retaining raw
    # MBI. Set a dedicated secret in deployed environments before importing;
    # the SECRET_KEY fallback keeps existing local setups working.
    KAIRON_IDENTITY_KEY = os.environ.get("KAIRON_IDENTITY_KEY", SECRET_KEY)
    # Session cookies must be Secure (HTTPS-only) per the doc's "TLS everywhere"
    # requirement. Defaults to True; only disable for local HTTP-only dev.
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", True)
    TEMP_PASSWORD_EXPIRY_HOURS = int(os.environ.get("TEMP_PASSWORD_EXPIRY_HOURS", "72"))

    # Private import storage. Secret is server-only; never a VITE_* variable.
    SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
    SUPABASE_STORAGE_SECRET_KEY = os.environ.get("SUPABASE_STORAGE_SECRET_KEY", "")
    SUPABASE_IMPORT_BUCKET = os.environ.get("SUPABASE_IMPORT_BUCKET", "hph-imports")
    IMPORT_EMBEDDED_PROCESSING = _env_bool("IMPORT_EMBEDDED_PROCESSING")
    IMPORT_MAX_FILE_BYTES = int(os.environ.get("IMPORT_MAX_FILE_BYTES", "50000000"))

    # Celery: key-rotation Beat schedule (§4b) and outbound mail (§5).
    CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
    CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")
    CELERY_TASK_ALWAYS_EAGER = _env_bool("CELERY_TASK_ALWAYS_EAGER")

    # §5: Flask-Mail's own expected config keys, entirely env-driven so
    # switching from local dev (Mailpit) to a real internal SMTP relay in
    # production is a config change, not a code change to the calling sites.
    MAIL_SERVER = os.environ.get("EMAIL_HOST", "localhost")
    MAIL_PORT = int(os.environ.get("EMAIL_PORT", "1025"))
    MAIL_USE_TLS = _env_bool("EMAIL_USE_TLS")
    MAIL_USE_SSL = _env_bool("EMAIL_USE_SSL")
    MAIL_USERNAME = os.environ.get("EMAIL_HOST_USER") or None
    MAIL_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD") or None
    MAIL_DEFAULT_SENDER = os.environ.get("DEFAULT_FROM_EMAIL", "noreply@vitalysehealth.local")
    MAIL_SUPPRESS_SEND = _env_bool("EMAIL_SUPPRESS_SEND")
    # Enable this outside local development so a missing secret/configuration
    # fails at startup instead of silently trying the local Mailpit defaults.
    MAIL_VALIDATE_CONFIG = _env_bool("EMAIL_VALIDATE_CONFIG")
    FRONTEND_LOGIN_URL = os.environ.get("FRONTEND_LOGIN_URL", "http://localhost:5173/login")

    # §4b debug aid: logs each request/response body decrypted - the
    # plaintext JSON, right after decrypt_request_body / right before
    # encrypt_response_body - to a running server's console. This includes
    # request bodies verbatim (e.g. login passwords). Off by default; local
    # dev only, never enable in production (it's a per-request log line for
    # every call, and it logs credentials).
    LOG_DECRYPTED_PAYLOADS = _env_bool("LOG_DECRYPTED_PAYLOADS")

    # The frontend (see ../frontend) is a separate-origin SPA that talks to
    # this API over a session cookie, so it needs CORS with credentials
    # enabled — not just a local-dev nicety, most real deployments put the
    # SPA and API on different (sub)domains too. Comma-separated in env so
    # prod can list its real frontend origin(s) without a code change.
    CORS_ALLOWED_ORIGINS = os.environ.get(
        "CORS_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")

    # flask-smorest / OpenAPI
    API_TITLE = "Vitalyse Health API"
    API_VERSION = "v1"
    OPENAPI_VERSION = "3.0.2"
    OPENAPI_URL_PREFIX = "/"
    OPENAPI_SWAGGER_UI_PATH = "/swagger-ui"
    OPENAPI_SWAGGER_UI_URL = "https://cdn.jsdelivr.net/npm/swagger-ui-dist/"
