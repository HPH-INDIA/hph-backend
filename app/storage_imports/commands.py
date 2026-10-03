import time
import click
from flask.cli import with_appcontext
from flask import current_app
from sqlalchemy.exc import SQLAlchemyError
from app.extensions import db
from app.storage_imports import storage
from app.storage_imports.services import run_once, cleanup_replaced_files


@click.command("process-file-imports")
@click.option("--once", is_flag=True, help="Process currently available imports and exit.")
@with_appcontext
def process_file_imports(once):
    """Run in a separate worker process; HTTP endpoints never execute imports."""
    next_cleanup = 0.0
    while True:
        try:
            run_once()
            if time.monotonic() >= next_cleanup:
                cleanup_replaced_files()
                next_cleanup = time.monotonic() + 300
        except SQLAlchemyError:
            db.session.remove()
            current_app.logger.warning("Import worker database connection interrupted; retrying.")
            if once:
                raise click.ClickException("Import worker could not access the database.")
        if once:
            return
        time.sleep(5)


@click.command("setup-import-storage")
@with_appcontext
def setup_import_storage():
    """Create/verify the private encrypted-import bucket using backend credentials."""
    from urllib.parse import quote
    bucket = current_app.config["SUPABASE_IMPORT_BUCKET"]
    config = {"id": bucket, "name": bucket, "public": False,
              "file_size_limit": current_app.config["IMPORT_MAX_FILE_BYTES"],
              "allowed_mime_types": ["application/octet-stream"]}
    try:
        existing = storage._request("GET", "/bucket/" + quote(bucket, safe=""), missing_ok=True)
        if existing:
            if existing.get("public"):
                raise click.ClickException("Import bucket is public. Use a dedicated private bucket.")
            storage._request("PUT", "/bucket/" + quote(bucket, safe=""), config)
        else:
            storage._request("POST", "/bucket", config)
        verified = storage._request("GET", "/bucket/" + quote(bucket, safe=""))
        if verified.get("public"):
            raise click.ClickException("Could not verify private bucket access.")
    except storage.StorageUnavailable as exc:
        raise click.ClickException(str(exc)) from None
    click.echo(f"Private import bucket {bucket} is ready.")
