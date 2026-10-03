import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flask import current_app
from flask_smorest import abort
from marshmallow import ValidationError
from sqlalchemy import text
from werkzeug.exceptions import HTTPException
from app.extensions import db
from app.encryption.crypto import wrap_key, unwrap_key
from app.storage_imports.models import StorageImport, StorageImportSlot
from app.storage_imports import storage

BLOCK_ROWS = 1000
ACTIVE = ("uploading", "queued", "processing")


def now():
    return datetime.now(timezone.utc)


def progress(job):
    from app.kairon.models import KaironUploadBatch
    from app.manual_daily_records.models import ManualImportBatch
    model = KaironUploadBatch if job.kind == "kairon" else ManualImportBatch
    batch = db.session.get(model, job.batch_id) if job.batch_id else None
    result = {"id": job.id, "kind": job.kind, "sourceFilename": job.source_filename,
              "status": job.status, "totalRows": job.total_rows, "error": job.error,
              "createdAt": job.created_at.isoformat(),
              "completedAt": job.completed_at.isoformat() if job.completed_at else None}
    for name in ("processed", "inserted", "created", "updated", "unchanged", "rejected", "unmatched"):
        result[name + "Count"] = getattr(batch, name + "_count", 0) if batch else 0
    return result


def _prepared(job):
    result = progress(job)
    if job.status == "uploading":
        job.updated_at = now()
    result.update(signedUrl=storage.sign_upload(job.object_path) if job.status == "uploading" else None,
                  encryptionKey=base64.b64encode(unwrap_key(job.wrapped_key)).decode() if job.status == "uploading" else None,
                  maxFileBytes=current_app.config["IMPORT_MAX_FILE_BYTES"])
    return result


def prepare(data, user_id):
    kind, request_id = data["kind"], str(data["request_id"])
    slot = StorageImportSlot.query.filter_by(kind=kind).with_for_update().one()
    existing = db.session.get(StorageImport, request_id)
    if existing:
        if existing.uploaded_by_id != user_id:
            abort(403, message="This upload request belongs to another manager.")
        if (existing.kind, existing.source_checksum, existing.total_rows) != (kind, data["source_checksum"].lower(), data["total_rows"]):
            abort(409, message="Upload request ID was reused with different content.")
        if slot.import_id != existing.id:
            abort(409, message="This upload has been replaced. Start a new upload.")
        try:
            result = _prepared(existing)
            db.session.commit()
            return result
        except storage.StorageUnavailable as exc:
            db.session.rollback()
            abort(503, message=str(exc))
    previous = db.session.get(StorageImport, slot.import_id) if slot.import_id else None
    if previous and previous.status in ACTIVE + ("failed",):
        abort(409, message=f"A {kind} import is already active or awaiting retry. Finish or abandon it first.")
    try:
        if previous:
            storage.delete(previous.object_path)
        job = StorageImport(id=request_id, kind=kind, uploaded_by_id=user_id,
                            source_filename=data["source_filename"], source_checksum=data["source_checksum"].lower(),
                            object_path=f"{kind}/{request_id}.hph-import", wrapped_key=wrap_key(AESGCM.generate_key(bit_length=256)),
                            total_rows=data["total_rows"], status="uploading")
        db.session.add(job)
        db.session.flush()
        slot.import_id = job.id
        # Generate authorization before committing. No usable job is left on a signing failure.
        result = _prepared(job)
        db.session.commit()
        return result
    except storage.StorageUnavailable as exc:
        db.session.rollback()
        abort(503, message=str(exc))


def upload_complete(job, data):
    job = StorageImport.query.filter_by(id=job.id).populate_existing().with_for_update().one()
    if job.status != "uploading":
        if job.file_checksum and not hmac.compare_digest(job.file_checksum, data["file_checksum"].lower()):
            abort(409, message="This import was already submitted with different content.")
        return progress(job)
    if data["file_size"] > current_app.config["IMPORT_MAX_FILE_BYTES"]:
        abort(413, message="The prepared upload exceeds the configured file size limit.")
    try:
        info = storage.exists(job.object_path)
    except storage.StorageUnavailable as exc:
        abort(503, message=str(exc))
    if not info:
        abort(409, message="The file has not finished uploading. Retry after the transfer completes.")
    actual_size = (info.get("metadata") or {}).get("size", info.get("size"))
    if actual_size is None or int(actual_size) != data["file_size"]:
        abort(422, message="The uploaded file size does not match.")
    job.file_size = data["file_size"]
    job.file_checksum = data["file_checksum"].lower()
    job.uploaded_at = now()
    job.status = "queued"
    db.session.commit()
    return progress(job)


def retry(job):
    slot = StorageImportSlot.query.filter_by(kind=job.kind).with_for_update().one()
    job = db.session.get(StorageImport, job.id, populate_existing=True)
    if slot.import_id != job.id:
        abort(409, message="This file has been replaced; select the file again.")
    if job.status == "failed":
        job.status, job.error, job.attempts = "queued", None, 0
        db.session.commit()
    elif job.status not in ("queued", "processing", "completed"):
        abort(409, message="Finish uploading the file before retrying processing.")
    return progress(job)


def abandon(job):
    slot = StorageImportSlot.query.filter_by(kind=job.kind).with_for_update().one()
    job = db.session.get(StorageImport, job.id, populate_existing=True)
    if job.status in ("queued", "processing"):
        abort(409, message="Wait for processing to finish before abandoning this import.")
    job.status = "abandoned"
    db.session.commit()
    return progress(job)


def decode_blocks(job, contents):
    """Authenticate every block against its import ID and position before any writes."""
    from app.kairon.schemas import KaironImportRowSchema
    from app.manual_daily_records.schemas import ManualImportRowSchema
    schema = KaironImportRowSchema() if job.kind == "kairon" else ManualImportRowSchema()
    aes = AESGCM(unwrap_key(job.wrapped_key))
    total = 0
    blocks = []
    manual_keys = set()
    for index, line in enumerate(contents.splitlines()):
        encrypted = base64.b64decode(line, validate=True)
        plain = aes.decrypt(encrypted[:12], encrypted[12:], f"hph-import:v1:{job.id}:{index}".encode())
        raw_rows = json.loads(plain)
        if not isinstance(raw_rows, list) or not 1 <= len(raw_rows) <= BLOCK_ROWS:
            raise ValueError("Invalid file block size.")
        if blocks and len(blocks[-1]) != BLOCK_ROWS:
            raise ValueError("Only the final block may contain fewer than 1,000 rows.")
        rows = []
        for row_index, row in enumerate(raw_rows):
            try:
                parsed = schema.load(row)
            except ValidationError:
                raise ValueError(f"Row {total + row_index + 1} failed validation. Correct the source file and upload again.") from None
            if job.kind == "manual":
                key = parsed["user_id"], parsed["record_date"]
                if key in manual_keys:
                    raise ValueError(f"Row {total + row_index + 1}: duplicate user/date in the file.")
                manual_keys.add(key)
            rows.append(parsed)
        total += len(rows)
        if total > job.total_rows:
            raise ValueError("File contains more rows than declared.")
        blocks.append(rows)
    if total != job.total_rows:
        raise ValueError("File row count does not match the declared total.")
    if job.kind == "manual":
        from app.manual_daily_records.services import manager_team_user_ids
        allowed = set(manager_team_user_ids(job.uploaded_by_id))
        if any(user_id not in allowed for user_id, _ in manual_keys):
            raise ValueError("One or more manual production rows are outside your team.")
        from app.users.models import User
        users = {user.id: user for user in User.query.filter(User.id.in_({key[0] for key in manual_keys})).all()}
        for user_id, date in manual_keys:
            user = users.get(user_id)
            if user is None:
                raise ValueError("One or more users in this file no longer exist.")
            if not user.is_active and user.last_working_day and date > user.last_working_day:
                raise ValueError("One or more rows fall after the user's last working day.")
    return blocks


@contextmanager
def kind_lock(kind):
    # Dedicated connection keeps a session advisory lock across importer commits.
    # PostgreSQL releases this automatically if the worker dies.
    with db.engine.connect() as connection:
        key = 73001 if kind == "kairon" else 73002
        locked = connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar()
        try:
            yield bool(locked)
        finally:
            if locked:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})


def process_job(job):
    from app.kairon.services import start_cumulative_import, process_import_chunk, complete_cumulative_import
    from app.manual_daily_records.services import start_manual_import, process_manual_import_chunk, complete_manual_import
    from app.kairon.models import KaironUploadBatch
    from app.manual_daily_records.models import ManualImportBatch
    try:
        from app.auth import has_feature, has_role
        from app.users.models import User
        owner = db.session.get(User, job.uploaded_by_id)
        if not owner or not owner.is_active or not has_role(owner, "manager") or not has_feature(owner, "reports", "write"):
            raise ValueError("One or more import permissions are no longer valid. Contact an administrator.")
        contents = storage.download(job.object_path)
        if job.file_size is not None and len(contents) != job.file_size:
            raise ValueError("The uploaded file size does not match.")
        checksum = hashlib.sha256(contents).hexdigest()
        if job.file_checksum and not hmac.compare_digest(checksum, job.file_checksum):
            raise ValueError("The uploaded file checksum does not match.")
        blocks = decode_blocks(job, contents)
        lines = contents.splitlines()
        job.file_size, job.file_checksum = len(contents), checksum
        job.status, job.error = "processing", None
        job.attempts += 1
        db.session.commit()
        if not job.batch_id:
            start = start_cumulative_import if job.kind == "kairon" else start_manual_import
            batch = start(job.source_filename, job.source_checksum, job.total_rows, job.uploaded_by_id, commit=False)
            job.batch_id = batch.id
            db.session.commit()
        model = KaironUploadBatch if job.kind == "kairon" else ManualImportBatch
        batch = db.session.get(model, job.batch_id)
        for index, rows in enumerate(blocks):
            # Each chunk commits records, history and batch progress atomically.
            # Resumption consults authoritative batch progress, not a browser offset.
            end = min((index + 1) * BLOCK_ROWS, job.total_rows)
            if batch.processed_count >= end:
                continue
            block_hash = hashlib.sha256(lines[index]).hexdigest()
            if job.kind == "kairon":
                batch, _ = process_import_chunk(batch.id, index, block_hash, rows)
            else:
                batch, _ = process_manual_import_chunk(batch.id, index, block_hash, rows, job.uploaded_by_id)
        if job.kind == "kairon":
            complete_cumulative_import(batch.id)
        else:
            complete_manual_import(batch.id, job.uploaded_by_id)
        job.status, job.completed_at = "completed", now()
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        job = db.session.get(StorageImport, job.id)
        # Never expose payloads, provider response bodies, or raw exception text.
        if isinstance(exc, ValueError):
            message = str(exc) if str(exc).startswith(("Row ", "File ", "The uploaded", "One or more")) else "The uploaded file is invalid or could not be decrypted. Upload a corrected file."
        elif isinstance(exc, HTTPException):
            message = (getattr(exc, "data", None) or {}).get("message", "Import validation failed.")
        else:
            message = "Import processing was interrupted. Retry to continue from saved progress."
        job.status, job.error = "failed", message[:500]
        db.session.commit()


def run_once():
    """Poll durable jobs; also recover uploads whose browser callback was lost."""
    for kind in ("kairon", "manual"):
        with kind_lock(kind) as acquired:
            if not acquired:
                continue
            slot = StorageImportSlot.query.filter_by(kind=kind).populate_existing().with_for_update().one_or_none()
            job = db.session.get(StorageImport, slot.import_id) if slot and slot.import_id else None
            if not job:
                continue
            if job.status == "uploading":
                try:
                    info = storage.exists(job.object_path)
                    if info:
                        job.status, job.uploaded_at = "queued", now()
                        db.session.commit()
                    elif now() - job.updated_at > timedelta(hours=3):
                        job.status, job.error = "abandoned", "Upload authorization expired. Select the file again."
                        db.session.commit()
                except storage.StorageUnavailable:
                    db.session.rollback()
                    continue
            db.session.commit()
            if job.status in ("queued", "processing"):
                process_job(job)
    db.session.remove()


def cleanup_replaced_files():
    """Late completion of an old TUS session must not retain an obsolete object."""
    for kind in ("kairon", "manual"):
        with kind_lock(kind) as acquired:
            if not acquired:
                continue
            slot = StorageImportSlot.query.filter_by(kind=kind).with_for_update().one_or_none()
            old = StorageImport.query.filter(StorageImport.kind == kind,
                StorageImport.status.in_(("completed", "abandoned")),
                StorageImport.id != (slot.import_id if slot else "")).all()
            try:
                for job in old:
                    storage.delete(job.object_path)
            except storage.StorageUnavailable:
                pass
            finally:
                db.session.rollback()
    db.session.remove()
