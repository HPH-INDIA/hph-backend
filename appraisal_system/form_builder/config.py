# ruff: noqa: E402
import os

from config_dbname import DB_SCHEMA  # noqa: F401, I001


SERVICE_AUDIENCE = "hph-form-builder"

# Base URL of the Access/BFF monolith, which serves the directory API.
ACCESS_SERVICE_URL = os.environ.get("ACCESS_SERVICE_URL")

# The only service allowed to resolve forms and validate answers without a user.
APPRAISAL_SERVICE_ISSUER = "hph-appraisal"
