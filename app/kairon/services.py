"""Kairon ingestion for legacy snapshots and cumulative chunked imports.

Patient names are rejected. Raw MBI reaches only `_row_identity`, where it
is converted to a keyed fingerprint; neither the raw value nor Patient is
stored in a model or returned in an API response.
"""
import csv
import hashlib
import hmac
import io
import re
from datetime import datetime, timezone

from flask import current_app
from flask_smorest import abort

from app.extensions import db
from app.kairon.models import (
    KaironChartAnalystAction,
    KaironChartHistory,
    KaironChartRecord,
    KaironImportChunk,
    KaironUploadBatch,
)
from app.users.models import User

# The cumulative-import template includes MBI for idempotent matching but
# deliberately excludes Patient.
TEMPLATE_COLUMNS = (
    "MBI",
    "Program",
    "Level",
    "Status",
    "Coding Analyst",
    "Actions",
    "Last Action",
    "Created",
    "Completed",
    "TAT",
    "Age",
    "Practice",
)

# Kairon's export always suffixes the analyst's name with their role,
# e.g. "Charishma Sonani - HPH Coding Analyst" - strip everything from the
# first " - " onward once here rather than in every caller.
_ANALYST_SUFFIX_RE = re.compile(r"\s*-\s*.*$")


def build_upload_template_csv():
    """The blank reference file a manager downloads before a bulk upload -
    header row only, in the one order the upload schema accepts."""
    buffer = io.StringIO()
    csv.writer(buffer).writerow(TEMPLATE_COLUMNS)
    return buffer.getvalue()


def normalize_analyst_name(raw_name):
    """'Charishma Sonani - HPH Coding Analyst' -> 'Charishma Sonani'."""
    return _ANALYST_SUFFIX_RE.sub("", raw_name or "").strip()


def resolve_user(raw_name):
    """Looks up `raw_name` against the platform's login identity (User),
    comparing its first/last name case- and whitespace-insensitively.
    Returns the User, or None if nothing matches - a row whose analyst
    doesn't resolve is never saved (see import_batch) rather than
    guessing or silently creating a new account. Create the User first,
    then re-upload the row.

    This resolves against User directly, not the cohorts feature's
    separate Coder entity - see the module docstring.
    """
    name = normalize_analyst_name(raw_name)
    if not name:
        return None

    full_name = db.func.lower(db.func.trim(User.first_name) + " " + db.func.trim(User.last_name))
    return User.query.filter(full_name == name.lower()).first()


def _resolve_chunk_users(rows):
    """Resolve every distinct analyst in a chunk with one user query."""
    requested_names = {normalize_analyst_name(row["coding_analyst"]).lower() for row in rows}
    requested_names.discard("")
    if not requested_names:
        return {}

    users_by_name = {}
    for user in User.query.order_by(User.id.asc()).all():
        full_name = " ".join(part for part in (user.first_name.strip(), user.last_name.strip()) if part).lower()
        if full_name in requested_names:
            # Match resolve_user's first-row behavior if duplicate names exist.
            users_by_name.setdefault(full_name, user)
    return users_by_name


def _supersede_existing_batches(as_of_date, new_batch_id):
    now = datetime.now(timezone.utc)
    stale_batches = KaironUploadBatch.query.filter(
        KaironUploadBatch.as_of_date == as_of_date,
        KaironUploadBatch.id != new_batch_id,
        KaironUploadBatch.superseded_at.is_(None),
    ).all()
    for stale in stale_batches:
        stale.superseded_at = now
        stale.superseded_by_id = new_batch_id


def import_batch(as_of_date, rows, uploaded_by_id, source_filename=None):
    """Creates one KaironUploadBatch and its KaironChartRecord (+
    KaironChartAnalystAction) rows from an already-validated row list
    (KaironUploadRequestSchema's `rows` output).

    A row whose Coding Analyst doesn't resolve to an existing User is
    never saved - user_id is a required field on both KaironChartRecord
    and KaironChartAnalystAction, so there is nothing to save it as.
    Such rows are skipped and their raw names are collected on the
    returned batch (as `unmatched_names`, alongside the persisted
    `unmatched_count`) so the caller can report them; fix the row by
    creating the User (or correcting the name) and re-uploading.

    There is no patient/MBI identifier to de-duplicate an individual row
    against a prior upload, so re-uploading for the same as_of_date does
    not try to merge row-by-row: it supersedes the prior batch(es) for
    that date (kept, not deleted, for audit - see
    _supersede_existing_batches) and this batch becomes the one active
    queries read (KaironChartRecord queries in routes.py always filter to
    batch.superseded_at IS NULL). Correcting a bad upload is therefore
    "upload the corrected file with the same as-of date", not a
    row-level edit screen - matching the rule that only a manager may
    change this data at all (enforced at the route layer via
    the Reports write and Manager-role guards, not here).
    """
    batch = KaironUploadBatch(
        as_of_date=as_of_date,
        source_filename=source_filename,
        uploaded_by_id=uploaded_by_id,
        row_count=len(rows),
    )
    db.session.add(batch)
    db.session.flush()  # assigns batch.id

    matched_count = 0
    unmatched_names = []
    completed_user_ids = set()
    for row in rows:
        user = resolve_user(row["coding_analyst"])
        if user is None:
            unmatched_names.append(row["coding_analyst"])
            continue

        record = KaironChartRecord(
            batch_id=batch.id,
            program=row["program"],
            level=row["level"],
            status=row["status"],
            user_id=user.id,
            coding_analyst_raw=row["coding_analyst"],
            actions=row.get("actions", 0),
            last_action=row.get("last_action"),
            created_date=row["created_date"],
            completed_date=row.get("completed_date"),
            tat_days=row.get("tat_days"),
            age_days=row.get("age_days"),
            practice=row.get("practice"),
        )
        db.session.add(record)
        db.session.flush()  # assigns record.id, needed below
        if row["status"] == "Completed" and row.get("completed_date") is not None:
            completed_user_ids.add(user.id)

        # Today's export gives one action per row; recorded as sequence 1
        # of what can later become a full multi-analyst chain (see
        # KaironChartAnalystAction's docstring) without a schema change.
        db.session.add(
            KaironChartAnalystAction(
                chart_record_id=record.id,
                user_id=user.id,
                level=row["level"],
                action_text=row.get("last_action"),
                action_date=row.get("completed_date") or row["created_date"],
                sequence_no=1,
            )
        )
        matched_count += 1

    batch.matched_count = matched_count
    batch.unmatched_count = len(unmatched_names)
    # Not a mapped column - transient, dump-only reporting for this
    # request's response (KaironUploadBatchSchema.unmatched_names) since
    # the skipped rows themselves are never persisted.
    batch.unmatched_names = unmatched_names
    _supersede_existing_batches(as_of_date, batch.id)
    db.session.commit()

    if completed_user_ids:
        from app.cohorts.models import CohortMembership
        from app.cohorts.services import compute_user_stage_periods

        memberships = CohortMembership.query.filter(CohortMembership.user_id.in_(completed_user_ids)).all()
        for membership in memberships:
            compute_user_stage_periods(membership)
        if memberships:
            db.session.commit()
    return batch


def start_cumulative_import(source_filename, file_checksum, total_rows, uploaded_by_id):
    """Start a resumable cumulative import; no chart data is written yet."""
    batch = KaironUploadBatch(
        as_of_date=None,
        source_filename=source_filename,
        file_checksum=file_checksum.lower(),
        total_rows=total_rows,
        row_count=total_rows,
        uploaded_by_id=uploaded_by_id,
        status="uploading",
    )
    db.session.add(batch)
    db.session.commit()
    return batch


def _normalized(value):
    return " ".join(str(value or "").strip().upper().split())


def _normalize_program(value):
    normalized = _normalized(value)
    if normalized == "PVP":
        return "PVP"
    if "FOUNDATION" in normalized:
        return "FOUNDATION"
    return str(value or "").strip()


def _fingerprint(value):
    secret = current_app.config["KAIRON_IDENTITY_KEY"].encode("utf-8")
    return hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()


def _row_identity(row):
    """Build a stable key without persisting the beneficiary identifier.

    Coder, status, completion date, TAT and age are deliberately excluded:
    all can change as the same chart moves through the workflow.
    """
    mbi = _normalized(row["mbi"]).replace("-", "").replace(" ", "")
    mbi_fingerprint = _fingerprint(f"mbi:v1:{mbi}")
    identity_parts = (
        mbi_fingerprint,
        _normalize_program(row["program"]),
        _normalized(row["level"]),
        row["created_date"].isoformat(),
        _normalized(row.get("practice")),
    )
    return _fingerprint("chart:v1:" + "|".join(identity_parts)), mbi_fingerprint


_MUTABLE_RECORD_FIELDS = (
    "program",
    "level",
    "status",
    "user_id",
    "coding_analyst_raw",
    "actions",
    "last_action",
    "created_date",
    "completed_date",
    "tat_days",
    "age_days",
    "practice",
)


def _record_values(row, user, batch_id):
    return {
        "batch_id": batch_id,
        "program": _normalize_program(row["program"]),
        "level": row["level"],
        "status": row["status"],
        "user_id": user.id,
        "coding_analyst_raw": row["coding_analyst"],
        "actions": row.get("actions", 0),
        "last_action": row.get("last_action"),
        "created_date": row["created_date"],
        "completed_date": row.get("completed_date"),
        "tat_days": row.get("tat_days"),
        "age_days": row.get("age_days"),
        "practice": row.get("practice"),
    }


def process_import_chunk(batch_id, chunk_number, checksum, rows):
    """Upsert one chunk and make retries of the same chunk a no-op."""
    batch = db.session.get(KaironUploadBatch, batch_id)
    if batch is None:
        abort(404, message="Kairon import was not found.")
    if batch.status == "completed":
        abort(409, message="This Kairon import is already complete.")

    existing_chunk = KaironImportChunk.query.filter_by(batch_id=batch_id, chunk_number=chunk_number).first()
    if existing_chunk is not None:
        if not hmac.compare_digest(existing_chunk.checksum, checksum.lower()):
            abort(409, message="This chunk number was already uploaded with different content.")
        return batch, existing_chunk

    now = datetime.now(timezone.utc)
    counts = {"inserted": 0, "updated": 0, "unchanged": 0, "rejected": 0, "unmatched": 0}

    users_by_name = _resolve_chunk_users(rows)
    prepared_rows = []
    identity_hashes = []
    for row in rows:
        analyst_name = normalize_analyst_name(row["coding_analyst"]).lower()
        user = users_by_name.get(analyst_name)
        if user is None:
            counts["unmatched"] += 1
            continue
        identity_hash, mbi_fingerprint = _row_identity(row)
        prepared_rows.append((row, user, identity_hash, mbi_fingerprint))
        identity_hashes.append(identity_hash)

    records_by_identity = {
        record.chart_identity_hash: record
        for record in KaironChartRecord.query.filter(KaironChartRecord.chart_identity_hash.in_(identity_hashes)).all()
    }

    for row, user, identity_hash, mbi_fingerprint in prepared_rows:
        record = records_by_identity.get(identity_hash)
        values = _record_values(row, user, batch.id)

        if record is None:
            record = KaironChartRecord(
                chart_identity_hash=identity_hash,
                mbi_fingerprint=mbi_fingerprint,
                first_seen_at=now,
                last_seen_at=now,
                **values,
            )
            db.session.add(record)
            records_by_identity[identity_hash] = record
            db.session.add(
                KaironChartHistory(
                    chart_record=record,
                    batch_id=batch.id,
                    previous_status=None,
                    status=record.status,
                    previous_user_id=None,
                    user_id=record.user_id,
                )
            )
            db.session.add(
                KaironChartAnalystAction(
                    chart_record=record,
                    user_id=user.id,
                    level=record.level,
                    action_text=record.last_action,
                    action_date=record.completed_date or record.created_date,
                    sequence_no=1,
                )
            )
            counts["inserted"] += 1
            continue

        previous_status = record.status
        previous_user_id = record.user_id
        changed = any(getattr(record, field) != values[field] for field in _MUTABLE_RECORD_FIELDS if field != "batch_id")
        if not changed:
            counts["unchanged"] += 1
            continue

        record.last_seen_at = now
        for field, value in values.items():
            setattr(record, field, value)
        record.updated_at = now
        db.session.add(
            KaironChartHistory(
                chart_record=record,
                batch_id=batch.id,
                previous_status=previous_status,
                status=record.status,
                previous_user_id=previous_user_id,
                user_id=record.user_id,
            )
        )
        counts["updated"] += 1

    chunk = KaironImportChunk(
        batch_id=batch.id,
        chunk_number=chunk_number,
        checksum=checksum.lower(),
        row_count=len(rows),
        inserted_count=counts["inserted"],
        updated_count=counts["updated"],
        unchanged_count=counts["unchanged"],
        rejected_count=counts["rejected"],
        unmatched_count=counts["unmatched"],
    )
    db.session.add(chunk)
    batch.processed_count += len(rows)
    batch.inserted_count += counts["inserted"]
    batch.updated_count += counts["updated"]
    batch.unchanged_count += counts["unchanged"]
    batch.rejected_count += counts["rejected"]
    batch.unmatched_count += counts["unmatched"]
    batch.matched_count += len(rows) - counts["unmatched"] - counts["rejected"]
    db.session.commit()
    return batch, chunk


def complete_cumulative_import(batch_id):
    batch = db.session.get(KaironUploadBatch, batch_id)
    if batch is None:
        abort(404, message="Kairon import was not found.")
    if batch.processed_count != batch.total_rows:
        abort(
            409,
            message=f"Upload is incomplete: processed {batch.processed_count} of {batch.total_rows} rows.",
        )
    batch.status = "completed"
    batch.completed_at = datetime.now(timezone.utc)
    db.session.commit()

    completed_user_ids = {
        user_id
        for (user_id,) in db.session.query(KaironChartRecord.user_id)
        .filter(
            KaironChartRecord.batch_id == batch.id,
            KaironChartRecord.status == "Completed",
            KaironChartRecord.completed_date.isnot(None),
        )
        .distinct()
        .all()
    }
    if completed_user_ids:
        from app.cohorts.models import CohortMembership
        from app.cohorts.services import compute_user_stage_periods

        memberships = CohortMembership.query.filter(CohortMembership.user_id.in_(completed_user_ids)).all()
        for membership in memberships:
            compute_user_stage_periods(membership)
        if memberships:
            db.session.commit()
    return batch
