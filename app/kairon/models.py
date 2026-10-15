"""Kairon chart-review records: the per-chart coding/QA ledger pulled from
Kairon exports (Program, Level, Status, Coding Analyst, Actions, Last
Action, Created, Completed, TAT, Age, Practice) - modeled directly on a
real `coding_ops_tasks` export.

Patient name is deliberately never modelled. Cumulative imports accept MBI
only long enough to calculate a keyed fingerprint; the raw value is never
persisted. New imports merge against `chart_identity_hash`, while the legacy
as-of-date batch fields remain temporarily for old audit rows and clients.
"""
from app.extensions import db

VALID_KAIRON_LEVELS = ("1LR", "2LR", "3LR")
# Observed in the real export: Active, On Hold, Completed. Kept as a plain
# CheckConstraint tuple (matching cohorts/models.py's VALID_PERIOD_SOURCES
# style) rather than a lookup table, since - unlike Stage - there's no
# other per-status metadata to hang off it; extending the set is a
# migration that widens this constraint.
VALID_KAIRON_STATUSES = ("Active", "On Hold", "Completed")


def _sql_in_list(values):
    return ", ".join(f"'{v}'" for v in values)


class KaironUploadBatch(db.Model):
    """One legacy snapshot or resumable cumulative import event."""

    __tablename__ = "kairon_upload_batches"

    id = db.Column(db.Integer, primary_key=True)
    # Legacy snapshot date. New cumulative imports do not require it; it is
    # retained temporarily so old audit rows and clients remain readable.
    as_of_date = db.Column(db.Date, nullable=True)
    source_filename = db.Column(db.String(255), nullable=True)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    uploaded_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)
    row_count = db.Column(db.Integer, nullable=False, default=0)
    matched_count = db.Column(db.Integer, nullable=False, default=0)
    unmatched_count = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(24), nullable=False, default="pending")
    file_checksum = db.Column(db.String(64), nullable=True)
    total_rows = db.Column(db.Integer, nullable=False, default=0)
    processed_count = db.Column(db.Integer, nullable=False, default=0)
    inserted_count = db.Column(db.Integer, nullable=False, default=0)
    updated_count = db.Column(db.Integer, nullable=False, default=0)
    unchanged_count = db.Column(db.Integer, nullable=False, default=0)
    rejected_count = db.Column(db.Integer, nullable=False, default=0)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    # Set when a later batch for the same as_of_date replaces this one.
    # Rows stay in place for audit rather than being deleted.
    superseded_at = db.Column(db.DateTime(timezone=True), nullable=True)
    superseded_by_id = db.Column(db.Integer, db.ForeignKey("kairon_upload_batches.id"), nullable=True)

    uploaded_by = db.relationship("User")
    superseded_by = db.relationship("KaironUploadBatch", remote_side=[id])

    def __repr__(self):
        return f"<KaironUploadBatch id={self.id} as_of={self.as_of_date}>"


class KaironChartRecord(db.Model):
    __tablename__ = "kairon_chart_records"
    __table_args__ = (
        db.CheckConstraint(
            "status = 'On Hold' OR (last_hold_reason IS NULL AND held_on IS NULL)",
            name="ck_kairon_hold_metadata_status",
        ),
        db.CheckConstraint(
            f"level IN ({_sql_in_list(VALID_KAIRON_LEVELS)})", name="ck_kairon_chart_records_level"
        ),
        db.CheckConstraint(
            f"status IN ({_sql_in_list(VALID_KAIRON_STATUSES)})", name="ck_kairon_chart_records_status"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("kairon_upload_batches.id"), nullable=False)
    chart_identity_hash = db.Column(db.String(64), nullable=True, unique=True)
    mbi_fingerprint = db.Column(db.String(64), nullable=True, index=True)

    program = db.Column(db.String(64), nullable=False)
    level = db.Column(db.String(16), nullable=False)
    status = db.Column(db.String(32), nullable=False)

    # Required: a row whose Coding Analyst name doesn't resolve to a User
    # is never saved (see services.import_batch), so there is no "pending
    # resolution" state to represent here. The raw string is still kept
    # alongside it for audit, matching Phase 1.5's personnel_id/employee_name
    # handling. Resolves to the platform's login identity (User), not
    # cohorts' separate Coder entity - see services.resolve_user() and the
    # module docstring.
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    coding_analyst_raw = db.Column(db.String(255), nullable=False)

    actions = db.Column(db.Integer, nullable=False, default=0)
    last_action = db.Column(db.Text, nullable=True)
    last_hold_reason = db.Column(db.Text, nullable=True)
    held_on = db.Column(db.Date, nullable=True)
    created_date = db.Column(db.Date, nullable=False)
    completed_date = db.Column(db.Date, nullable=True)
    tat_days = db.Column(db.Integer, nullable=True)
    age_days = db.Column(db.Integer, nullable=True)
    practice = db.Column(db.String(255), nullable=True)

    created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)
    first_seen_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)
    last_seen_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True), server_default=db.func.now(), onupdate=db.func.now(), nullable=False
    )

    batch = db.relationship("KaironUploadBatch", backref="records")
    user = db.relationship("User", backref="kairon_chart_records")

    def __repr__(self):
        return f"<KaironChartRecord id={self.id} level={self.level} status={self.status}>"


class KaironChartAnalystAction(db.Model):
    """A chart's per-touch analyst history - a genuine many-to-many
    between chart records and users (one chart can, over its life, be
    touched by several analysts across 1LR/2LR/3LR; one analyst touches
    many charts), kept as its own table per the requirement that Kairon
    records link into the user identity many-to-many.

    Today's export only ever gives one action per uploaded row (the
    row's own Actions/Last Action/Coding Analyst), so import_batch()
    records exactly that one action now - this table is ready to hold a
    full multi-analyst handoff chain the day Kairon's export includes one,
    without a schema change.
    """

    __tablename__ = "kairon_chart_analyst_actions"

    id = db.Column(db.Integer, primary_key=True)
    chart_record_id = db.Column(db.Integer, db.ForeignKey("kairon_chart_records.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    level = db.Column(db.String(16), nullable=False)
    action_text = db.Column(db.Text, nullable=True)
    action_date = db.Column(db.Date, nullable=True)
    sequence_no = db.Column(db.Integer, nullable=False, default=1)

    chart_record = db.relationship("KaironChartRecord", backref="analyst_actions")
    user = db.relationship("User")

    def __repr__(self):
        return f"<KaironChartAnalystAction chart_record_id={self.chart_record_id} user_id={self.user_id}>"


class KaironImportChunk(db.Model):
    """Idempotency and audit record for one client upload chunk."""

    __tablename__ = "kairon_import_chunks"
    __table_args__ = (db.UniqueConstraint("batch_id", "chunk_number", name="uq_kairon_import_chunk"),)

    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("kairon_upload_batches.id"), nullable=False)
    chunk_number = db.Column(db.Integer, nullable=False)
    checksum = db.Column(db.String(64), nullable=False)
    row_count = db.Column(db.Integer, nullable=False, default=0)
    inserted_count = db.Column(db.Integer, nullable=False, default=0)
    updated_count = db.Column(db.Integer, nullable=False, default=0)
    unchanged_count = db.Column(db.Integer, nullable=False, default=0)
    rejected_count = db.Column(db.Integer, nullable=False, default=0)
    unmatched_count = db.Column(db.Integer, nullable=False, default=0)
    processed_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)

    batch = db.relationship("KaironUploadBatch", backref="chunks")


class KaironChartHistory(db.Model):
    """A compact audit trail for status and analyst changes across exports."""

    __tablename__ = "kairon_chart_history"

    id = db.Column(db.Integer, primary_key=True)
    chart_record_id = db.Column(db.Integer, db.ForeignKey("kairon_chart_records.id"), nullable=False)
    batch_id = db.Column(db.Integer, db.ForeignKey("kairon_upload_batches.id"), nullable=False)
    previous_status = db.Column(db.String(32), nullable=True)
    status = db.Column(db.String(32), nullable=False)
    previous_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    changed_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now(), nullable=False)

    chart_record = db.relationship("KaironChartRecord", backref="history")
