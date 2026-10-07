from marshmallow import Schema, fields, validate

from app.responses import envelope_schema


class LoginHoursUploadRequestSchema(Schema):
    source_filename = fields.String(required=True, validate=validate.Length(min=1, max=255), data_key="sourceFilename")
    file_base64 = fields.String(required=True, validate=validate.Length(min=1, max=15_000_000), data_key="fileBase64")


class LoginHoursRecordChunkSchema(Schema):
    source_filename = fields.String(required=True, validate=validate.Length(min=1, max=255), data_key="sourceFilename")
    source_format = fields.String(required=True, validate=validate.Length(min=1, max=64), data_key="sourceFormat")
    upload_id = fields.UUID(required=True, data_key="uploadId")
    batch_id = fields.Integer(load_default=None, allow_none=True, validate=validate.Range(min=1), data_key="batchId")
    chunk_index = fields.Integer(required=True, validate=validate.Range(min=0), data_key="chunkIndex")
    headers = fields.List(fields.String(allow_none=True), required=True, validate=validate.Length(min=1, max=100))
    rows = fields.List(fields.List(fields.Raw(allow_none=True), validate=validate.Length(max=100)), required=True, validate=validate.Length(min=1, max=200))


class LoginHoursUploadBatchSchema(Schema):
    id = fields.Integer(dump_only=True)
    source_filename = fields.String(dump_only=True, data_key="sourceFilename")
    source_format = fields.String(dump_only=True, data_key="sourceFormat")
    uploaded_by_id = fields.Integer(dump_only=True, data_key="uploadedById")
    uploaded_at = fields.DateTime(dump_only=True, data_key="uploadedAt")
    row_count = fields.Integer(dump_only=True, data_key="rowCount")
    matched_count = fields.Integer(dump_only=True, data_key="matchedCount")
    unmatched_count = fields.Integer(dump_only=True, data_key="unmatchedCount")
    unmatched_names = fields.List(fields.String(), dump_only=True, data_key="unmatchedNames")


class LoginHourRecordQuerySchema(Schema):
    page = fields.Integer(load_default=1, validate=validate.Range(min=1))
    page_size = fields.Integer(load_default=25, validate=validate.Range(min=1, max=100), data_key="pageSize")
    from_date = fields.Date(load_default=None, allow_none=True, data_key="from")
    to_date = fields.Date(load_default=None, allow_none=True, data_key="to")
    user_id = fields.Integer(load_default=None, allow_none=True, data_key="userId")
    user_ids = fields.List(fields.Integer(validate=validate.Range(min=1)), load_default=list, data_key="userIds")
    project_id = fields.Integer(load_default=None, allow_none=True, data_key="projectId")
    lead_id = fields.Integer(load_default=None, allow_none=True, data_key="leadId")
    cohort_id = fields.Integer(load_default=None, allow_none=True, data_key="cohortId")


class LoginHourRecordSchema(Schema):
    project_name = fields.Method("get_project_name", dump_only=True, data_key="projectName")

    def get_project_name(self, record):
        return record.user.project.name if record.user.project else None

    id = fields.Integer(dump_only=True)
    batch_id = fields.Integer(dump_only=True, data_key="batchId")
    user_id = fields.Integer(dump_only=True, data_key="userId")
    user_name = fields.Method("get_user_name", dump_only=True, data_key="userName")
    attendance_date = fields.Date(dump_only=True, data_key="date")
    employee_name_raw = fields.String(dump_only=True, data_key="employeeNameRaw")
    personnel_id = fields.String(dump_only=True, allow_none=True, data_key="personnelId")
    department = fields.String(dump_only=True, allow_none=True)
    first_in = fields.Time(dump_only=True, allow_none=True, data_key="firstIn")
    last_out = fields.Time(dump_only=True, allow_none=True, data_key="lastOut")
    total_inside_minutes = fields.Integer(dump_only=True, data_key="totalInsideMinutes")
    total_outside_minutes = fields.Integer(dump_only=True, data_key="totalOutsideMinutes")
    total_span_minutes = fields.Integer(dump_only=True, data_key="totalSpanMinutes")
    entries = fields.Integer(dump_only=True)
    exits = fields.Integer(dump_only=True)
    status = fields.String(dump_only=True, allow_none=True)
    anomalies = fields.Integer(dump_only=True)

    def get_user_name(self, record):
        return f"{record.user.first_name} {record.user.last_name}"


class LoginHourRecordPageSchema(Schema):
    items = fields.List(fields.Nested(LoginHourRecordSchema), dump_only=True)
    page = fields.Integer(dump_only=True)
    page_size = fields.Integer(dump_only=True, data_key="pageSize")
    total = fields.Integer(dump_only=True)
    total_pages = fields.Integer(dump_only=True, data_key="totalPages")
    average_inside_minutes = fields.Float(dump_only=True, allow_none=True, data_key="averageInsideMinutes")
    filter_options = fields.Dict(dump_only=True, data_key="filterOptions")


LoginHoursUploadBatchEnvelopeSchema = envelope_schema(
    "LoginHoursUploadBatchEnvelopeSchema", fields.Nested(LoginHoursUploadBatchSchema)
)
LoginHoursUploadBatchListEnvelopeSchema = envelope_schema(
    "LoginHoursUploadBatchListEnvelopeSchema", fields.List(fields.Nested(LoginHoursUploadBatchSchema))
)
LoginHourRecordPageEnvelopeSchema = envelope_schema(
    "LoginHourRecordPageEnvelopeSchema", fields.Nested(LoginHourRecordPageSchema)
)
