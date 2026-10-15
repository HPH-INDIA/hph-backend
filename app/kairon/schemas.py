from flask_smorest import abort
from marshmallow import RAISE, Schema, fields, pre_load, validate

from app.kairon.models import VALID_KAIRON_LEVELS, VALID_KAIRON_STATUSES
from app.responses import envelope_schema

# Patient names are not needed for record identity and must never be sent or
# stored. MBI is accepted only by the import endpoint, where it is converted
# to a keyed fingerprint and immediately discarded.
_FORBIDDEN_COLUMN_ALIASES = {
    "patient",
    "patientname",
}


def _normalize_key(key):
    return "".join(ch for ch in str(key).lower() if ch.isalnum())


def reject_forbidden_columns(raw_row):
    if not isinstance(raw_row, dict):
        return raw_row
    hits = [key for key in raw_row if _normalize_key(key) in _FORBIDDEN_COLUMN_ALIASES]
    if hits:
        # Raised as an explicit 400 (via flask_smorest.abort) rather than a
        # marshmallow ValidationError, so this PHI-specific refusal doesn't
        # get folded into the generic per-field 422 validation-error shape -
        # it's a deliberate policy rejection, not a schema mismatch.
        abort(
            400,
            message=(
                "Patient name must never be uploaded to this system. "
                f"Remove these column(s) from the file before uploading: {', '.join(sorted(hits))}."
            ),
        )
    return raw_row


class KaironChartRowSchema(Schema):
    """One row of the upload payload - exactly the columns the reference
    template offers (see services.TEMPLATE_COLUMNS) and nothing else.
    Field order here mirrors the template one-for-one: Program, Level,
    Status, Coding Analyst, Actions, Last Action, Last Hold Reason, Held On, Created, Completed,
    TAT, Age, Practice. Patient and MBI have no field - see the module
    note above.
    """

    class Meta:
        unknown = RAISE

    program = fields.String(required=True, validate=validate.Length(min=1, max=64))
    level = fields.String(required=True, validate=validate.OneOf(VALID_KAIRON_LEVELS))
    status = fields.String(required=True, validate=validate.OneOf(VALID_KAIRON_STATUSES))
    coding_analyst = fields.String(
        required=True, validate=validate.Length(min=1, max=255), data_key="codingAnalyst"
    )
    actions = fields.Integer(required=True, validate=validate.Range(min=0))
    last_action = fields.String(required=True, allow_none=True, data_key="lastAction")
    last_hold_reason = fields.String(allow_none=True, data_key="lastHoldReason")
    held_on = fields.Date(allow_none=True, data_key="heldOn")
    created_date = fields.Date(required=True, data_key="created")
    completed_date = fields.Date(required=True, allow_none=True, data_key="completed")
    tat_days = fields.Integer(required=True, allow_none=True, data_key="tat")
    age_days = fields.Integer(required=True, allow_none=True, data_key="age")
    practice = fields.String(required=True, allow_none=True, validate=validate.Length(max=255))

    @pre_load
    def _guard_phi_columns(self, data, **kwargs):
        return reject_forbidden_columns(data)


class KaironImportRowSchema(KaironChartRowSchema):
    """Cumulative import row. Raw MBI is request-only and is never persisted."""

    mbi = fields.String(required=True, load_only=True, validate=validate.Length(min=1, max=64))


class KaironImportStartSchema(Schema):
    source_filename = fields.String(required=True, data_key="sourceFilename", validate=validate.Length(min=1, max=255))
    file_checksum = fields.String(required=True, data_key="fileChecksum", validate=validate.Regexp(r"^[a-fA-F0-9]{64}$"))
    total_rows = fields.Integer(required=True, data_key="totalRows", validate=validate.Range(min=1))


class KaironImportChunkSchema(Schema):
    checksum = fields.String(required=True, validate=validate.Regexp(r"^[a-fA-F0-9]{64}$"))
    rows = fields.List(fields.Nested(KaironImportRowSchema), required=True, validate=validate.Length(min=1, max=2000))


class KaironImportProgressSchema(Schema):
    id = fields.Integer(dump_only=True)
    status = fields.String(dump_only=True)
    source_filename = fields.String(dump_only=True, allow_none=True, data_key="sourceFilename")
    total_rows = fields.Integer(dump_only=True, data_key="totalRows")
    processed_count = fields.Integer(dump_only=True, data_key="processedCount")
    inserted_count = fields.Integer(dump_only=True, data_key="insertedCount")
    updated_count = fields.Integer(dump_only=True, data_key="updatedCount")
    unchanged_count = fields.Integer(dump_only=True, data_key="unchangedCount")
    rejected_count = fields.Integer(dump_only=True, data_key="rejectedCount")
    unmatched_count = fields.Integer(dump_only=True, data_key="unmatchedCount")
    uploaded_at = fields.DateTime(dump_only=True, data_key="uploadedAt")
    completed_at = fields.DateTime(dump_only=True, allow_none=True, data_key="completedAt")


KaironImportProgressEnvelopeSchema = envelope_schema(
    "KaironImportProgressEnvelopeSchema", fields.Nested(KaironImportProgressSchema)
)


class KaironUploadRequestSchema(Schema):
    """POST /api/kairon/uploads body. The frontend downloads the template,
    the manager fills it in and picks the as-of date, and the frontend
    converts the filled sheet to this JSON shape before posting - the
    backend never parses a raw file.
    """

    as_of_date = fields.Date(required=True, data_key="asOfDate")
    source_filename = fields.String(allow_none=True, load_default=None, data_key="sourceFilename")
    rows = fields.List(fields.Nested(KaironChartRowSchema), required=True, validate=validate.Length(min=1))

    @pre_load
    def _guard_phi_columns(self, data, **kwargs):
        return reject_forbidden_columns(data)


class KaironChartRecordSchema(Schema):
    id = fields.Integer(dump_only=True)
    batch_id = fields.Integer(dump_only=True, data_key="batchId")
    program = fields.String(dump_only=True)
    level = fields.String(dump_only=True)
    status = fields.String(dump_only=True)
    user_id = fields.Integer(dump_only=True, data_key="userId")
    coding_analyst = fields.String(dump_only=True, attribute="coding_analyst_raw", data_key="codingAnalyst")
    actions = fields.Integer(dump_only=True)
    last_action = fields.String(dump_only=True, allow_none=True, data_key="lastAction")
    last_hold_reason = fields.String(dump_only=True, allow_none=True, data_key="lastHoldReason")
    held_on = fields.Date(dump_only=True, allow_none=True, data_key="heldOn")
    created_date = fields.Date(dump_only=True, data_key="created")
    completed_date = fields.Date(dump_only=True, allow_none=True, data_key="completed")
    tat_days = fields.Integer(dump_only=True, allow_none=True, data_key="tat")
    age_days = fields.Integer(dump_only=True, allow_none=True, data_key="age")
    practice = fields.String(dump_only=True, allow_none=True)


class KaironChartQuerySchema(Schema):
    status = fields.String(required=False, load_default=None)
    level = fields.String(required=False, load_default=None)
    user_id = fields.Integer(required=False, load_default=None, data_key="userId")
    # Ad hoc "group of users" filter (§8): repeat the query param, e.g.
    # ?userIds=3&userIds=7. Cohort-based grouping isn't wired in here yet -
    # it needs the not-yet-built Coder.user_id bridge (see the doc's §9) -
    # so callers that want to filter by cohort resolve its members to user
    # ids themselves and pass them here.
    user_ids = fields.List(fields.Integer(), required=False, load_default=None, data_key="userIds")
    as_of_date = fields.Date(required=False, load_default=None, data_key="asOfDate")


class KaironUploadBatchSchema(Schema):
    id = fields.Integer(dump_only=True)
    as_of_date = fields.Date(dump_only=True, allow_none=True, data_key="asOfDate")
    source_filename = fields.String(dump_only=True, allow_none=True, data_key="sourceFilename")
    uploaded_by_id = fields.Integer(dump_only=True, data_key="uploadedById")
    uploaded_at = fields.DateTime(dump_only=True, data_key="uploadedAt")
    row_count = fields.Integer(dump_only=True, data_key="rowCount")
    matched_count = fields.Integer(dump_only=True, data_key="matchedCount")
    unmatched_count = fields.Integer(dump_only=True, data_key="unmatchedCount")
    # Only populated on the batch object import_batch() just built (a
    # transient, non-persisted attribute) - the skipped rows themselves are
    # never saved, so a later GET of this same batch won't have these names.
    unmatched_names = fields.List(fields.String(), dump_only=True, data_key="unmatchedNames")
    superseded_at = fields.DateTime(dump_only=True, allow_none=True, data_key="supersededAt")


KaironUploadBatchEnvelopeSchema = envelope_schema(
    "KaironUploadBatchEnvelopeSchema", fields.Nested(KaironUploadBatchSchema)
)
KaironUploadBatchListEnvelopeSchema = envelope_schema(
    "KaironUploadBatchListEnvelopeSchema", fields.List(fields.Nested(KaironUploadBatchSchema))
)
KaironChartRecordListEnvelopeSchema = envelope_schema(
    "KaironChartRecordListEnvelopeSchema", fields.List(fields.Nested(KaironChartRecordSchema))
)
