from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from common.configdb import get_sql_alchemy


db = get_sql_alchemy()

# JSONB on Postgres (Supabase), plain JSON elsewhere so tests can run on SQLite.
JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def utc_now():
    return datetime.now(timezone.utc)
