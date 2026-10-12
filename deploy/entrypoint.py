"""Construct the internal database URL without shell/URL escaping secrets."""
import os
import sys
from sqlalchemy.engine import URL

os.environ["DATABASE_URL"] = URL.create(
    "postgresql+psycopg2",
    username=os.environ["POSTGRES_USER"],
    password=os.environ["POSTGRES_PASSWORD"],
    host="database",
    port=5432,
    database=os.environ["POSTGRES_DB"],
).render_as_string(hide_password=False)
if len(sys.argv) < 2:
    raise SystemExit("A container command is required")
os.execvp(sys.argv[1], sys.argv[1:])
