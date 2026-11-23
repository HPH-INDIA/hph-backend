"""The plugin runs inside the monolith, so its tests reuse the monolith's fixtures: a real
Postgres database migrated with the full Alembic chain, seeded users and the encrypted API client.
"""

from tests.conftest import (  # noqa: F401 - re-exported as fixtures for this directory
    _clean_cohorts_tables,
    api_client,
    app,
    employee_user,
    lead_user,
    manager_user,
    superadmin,
)
