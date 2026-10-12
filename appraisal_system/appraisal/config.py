# ruff: noqa: E402
import os

from config_dbname import DB_SCHEMA  # noqa: F401, I001


SERVICE_AUDIENCE = "hph-appraisal"
FORM_BUILDER_AUDIENCE = "hph-form-builder"

# Base URLs of the services this one calls.
ACCESS_SERVICE_URL = os.environ.get("ACCESS_SERVICE_URL")
FORM_BUILDER_SERVICE_URL = os.environ.get("FORM_BUILDER_SERVICE_URL")

# Where links in notification emails point (the appraisal pages of the HPH frontend).
APPRAISAL_APP_URL = os.environ.get("APPRAISAL_APP_URL", "http://localhost:5173/appraisals")

# Mail delivery is not live yet (gap G-4), so notifications queue up until this is switched on.
NOTIFICATIONS_ENABLED = os.environ.get("NOTIFICATIONS_ENABLED", "false").lower() == "true"
NOTIFICATION_MAX_ATTEMPTS = int(os.environ.get("NOTIFICATION_MAX_ATTEMPTS", "5"))
REMINDER_DAYS_BEFORE = int(os.environ.get("REMINDER_DAYS_BEFORE", "3"))

SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"
SMTP_FROM = os.environ.get("SMTP_FROM", "appraisals@humanpoweredhealth.com")
