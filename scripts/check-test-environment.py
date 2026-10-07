"""Read-only checks for the configured Supabase database, keys, and bucket."""
from pathlib import Path
import sys
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from run import app
from app.extensions import db
from app.encryption.services import get_active_key
from app.storage_imports import storage


def main():
    with app.app_context():
        try:
            with db.engine.connect() as connection:
                assert connection.execute(text("SELECT 1")).scalar_one() == 1
            print("Supabase PostgreSQL: connected")
            get_active_key()
            print("Application encryption key: readable")
            bucket = storage._request("GET", "/bucket/" + quote(app.config["SUPABASE_IMPORT_BUCKET"], safe=""))
            if bucket.get("public") is not False:
                raise ValueError("The import bucket must be private.")
            print("Supabase import bucket: reachable and private")
        except Exception as error:
            # Provider exceptions can contain credentials or connection strings.
            print(f"Check failed ({type(error).__name__}). Verify the test database URL, existing encryption key, migrations, and private bucket configuration.", file=sys.stderr)
            return 1
        finally:
            db.session.remove()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
