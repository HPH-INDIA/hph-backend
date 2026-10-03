from marshmallow import Schema, fields, validate
from app.responses import envelope_schema


class PrepareSchema(Schema):
    kind = fields.String(required=True, validate=validate.OneOf(("kairon", "manual")))
    source_filename = fields.String(required=True, data_key="sourceFilename", validate=validate.Length(min=1, max=255))
    source_checksum = fields.String(required=True, data_key="sourceChecksum", validate=validate.Regexp(r"^[a-fA-F0-9]{64}$"))
    total_rows = fields.Integer(required=True, data_key="totalRows", validate=validate.Range(min=1, max=500_000))
    request_id = fields.UUID(required=True, data_key="requestId")


class CompleteSchema(Schema):
    file_size = fields.Integer(required=True, data_key="fileSize", validate=validate.Range(min=1))
    file_checksum = fields.String(required=True, data_key="fileChecksum", validate=validate.Regexp(r"^[a-fA-F0-9]{64}$"))


class ProgressSchema(Schema):
    id = fields.String()
    kind = fields.String()
    sourceFilename = fields.String()
    status = fields.String()
    totalRows = fields.Integer()
    processedCount = fields.Integer()
    insertedCount = fields.Integer()
    createdCount = fields.Integer()
    updatedCount = fields.Integer()
    unchangedCount = fields.Integer()
    rejectedCount = fields.Integer()
    unmatchedCount = fields.Integer()
    error = fields.String(allow_none=True)
    createdAt = fields.String()
    completedAt = fields.String(allow_none=True)


class PreparedSchema(ProgressSchema):
    signedUrl = fields.String(allow_none=True)
    encryptionKey = fields.String(allow_none=True)
    maxFileBytes = fields.Integer()


ProgressEnvelope = envelope_schema("StorageImportProgressEnvelope", fields.Nested(ProgressSchema))
PreparedEnvelope = envelope_schema("StorageImportPreparedEnvelope", fields.Nested(PreparedSchema))
ListEnvelope = envelope_schema("StorageImportListEnvelope", fields.List(fields.Nested(ProgressSchema)))
