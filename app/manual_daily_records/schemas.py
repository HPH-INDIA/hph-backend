from decimal import Decimal

from marshmallow import Schema, ValidationError, fields, validate, validates_schema

from app.manual_daily_records.models import MAX_HOURS_PER_FIELD, VALID_MEETING_TYPES, VALID_STATUSES
from app.responses import envelope_schema

def _hour_field(**kwargs):
    return fields.Decimal(places=2, validate=validate.Range(min=0, max=MAX_HOURS_PER_FIELD), **kwargs)


class ManualMeetingSchema(Schema):
    type = fields.String(required=True, validate=validate.OneOf(VALID_MEETING_TYPES))
    hours = fields.Decimal(required=True, as_string=True, places=2,
                           validate=validate.Range(min=Decimal("0.01"), max=MAX_HOURS_PER_FIELD))


class ManualDailyRecordUpsertSchema(Schema):
    """POST /api/manual-daily-records body. There's no user field - the
    record is always saved against whoever is logged in (see
    services.upsert_own_record) - and no batch/as-of date, just the one
    day this entry is for.
    """

    record_date = fields.Date(required=True, data_key="date")
    pvp_count = fields.Integer(load_default=None, allow_none=True, validate=validate.Range(min=0), data_key="pvpCount")
    foundation_count = fields.Integer(
        load_default=None, allow_none=True, validate=validate.Range(min=0), data_key="foundationCount"
    )
    # Temporary compatibility for older clients. New clients submit the two
    # program counts; the server always derives the total.
    production_count = fields.Integer(
        load_default=None, allow_none=True, validate=validate.Range(min=0), data_key="productionCount"
    )
    tech_issues_downtime_hours = _hour_field(required=True, data_key="techIssuesDowntimeHours")
    no_inventory_idle_time_hours = _hour_field(required=True, data_key="noInventoryIdleTimeHours")
    leave_hours = _hour_field(required=True, data_key="leaveHours")
    meeting_engagement_hours = _hour_field(required=True, data_key="meetingEngagementHours")
    meeting_type = fields.String(
        allow_none=True, validate=validate.OneOf(VALID_MEETING_TYPES), data_key="meetingType"
    )
    meetings = fields.List(fields.Nested(ManualMeetingSchema), required=False,
                           validate=validate.Length(max=20))

    @validates_schema
    def derive_production_count(self, data, **kwargs):
        pvp_count = data.get("pvp_count")
        foundation_count = data.get("foundation_count")
        legacy_total = data.get("production_count")
        if pvp_count is None and foundation_count is None:
            if legacy_total is None:
                raise ValidationError(
                    "PVP count and Foundation count are required.",
                    field_name="pvpCount",
                )
            pvp_count, foundation_count = legacy_total, 0
        else:
            pvp_count = pvp_count or 0
            foundation_count = foundation_count or 0
        data["pvp_count"] = pvp_count
        data["foundation_count"] = foundation_count
        data["production_count"] = pvp_count + foundation_count

        if "meetings" in data:
            meetings = data["meetings"]
            total = sum((meeting["hours"] for meeting in meetings), Decimal("0"))
            if total > MAX_HOURS_PER_FIELD:
                raise ValidationError("Total meeting hours cannot exceed 10.", field_name="meetings")
            if total != data["meeting_engagement_hours"]:
                raise ValidationError("Meeting hours must equal the sum of the meeting entries.",
                                      field_name="meetingEngagementHours")
            single_type = meetings[0]["type"] if len(meetings) == 1 else None
            if "meeting_type" in data and data["meeting_type"] != single_type:
                raise ValidationError("Meeting type must match the meeting entries.", field_name="meetingType")
            data["meeting_type"] = single_type


class ManualDailyRecordSchema(Schema):
    id = fields.Integer(dump_only=True)
    user_id = fields.Integer(dump_only=True, data_key="userId")
    record_date = fields.Date(dump_only=True, data_key="date")
    production_count = fields.Integer(dump_only=True, data_key="productionCount")
    pvp_count = fields.Integer(dump_only=True, data_key="pvpCount")
    foundation_count = fields.Integer(dump_only=True, data_key="foundationCount")
    tech_issues_downtime_hours = fields.Decimal(dump_only=True, as_string=True, data_key="techIssuesDowntimeHours")
    no_inventory_idle_time_hours = fields.Decimal(
        dump_only=True, as_string=True, data_key="noInventoryIdleTimeHours"
    )
    leave_hours = fields.Decimal(dump_only=True, as_string=True, data_key="leaveHours")
    meeting_engagement_hours = fields.Decimal(dump_only=True, as_string=True, data_key="meetingEngagementHours")
    meeting_type = fields.String(dump_only=True, allow_none=True, data_key="meetingType")
    meetings = fields.Method("get_meetings", dump_only=True)
    status = fields.String(dump_only=True)
    reviewed_by_id = fields.Integer(dump_only=True, allow_none=True, data_key="reviewedById")
    reviewed_at = fields.DateTime(dump_only=True, allow_none=True, data_key="reviewedAt")
    rejection_reason = fields.String(dump_only=True, allow_none=True, data_key="rejectionReason")
    created_at = fields.DateTime(dump_only=True, data_key="createdAt")
    updated_at = fields.DateTime(dump_only=True, data_key="updatedAt")

    daily_target = fields.Integer(dump_only=True, allow_none=True, data_key="dailyTarget")
    adjusted_cpd = fields.Decimal(
        dump_only=True, as_string=True, places=2, allow_none=True, data_key="adjustedCpd"
    )

    def get_meetings(self, record):
        if record.meetings is not None:
            return record.meetings
        if record.meeting_engagement_hours and record.meeting_engagement_hours > 0:
            return [{"type": record.meeting_type, "hours": str(record.meeting_engagement_hours)}]
        return []


class ManualDailyRecordQuerySchema(Schema):
    """Backs all four read patterns from §8: a date range, a single user, a
    named group (as an ad hoc user-id list - see the Kairon doc's identical
    fallback, since the Cohort<->User bridge doesn't exist yet), and
    "everyone except" a given set. A manager's review queue is just this
    same listing filtered to status=pending.
    """

    from_date = fields.Date(required=False, load_default=None, data_key="fromDate")
    to_date = fields.Date(required=False, load_default=None, data_key="toDate")
    user_id = fields.Integer(required=False, load_default=None, data_key="userId")
    user_ids = fields.List(fields.Integer(), required=False, load_default=None, data_key="userIds")
    exclude_user_ids = fields.List(fields.Integer(), required=False, load_default=None, data_key="excludeUserIds")
    lead_id = fields.Integer(required=False, load_default=None, data_key="leadId")
    status = fields.String(required=False, load_default=None, validate=validate.OneOf(VALID_STATUSES))


class RejectManualDailyRecordSchema(Schema):
    reason = fields.String(required=False, load_default=None, allow_none=True)


class ManualBulkUploadRequestSchema(Schema):
    record_date = fields.Date(required=True, data_key="recordDate")
    source_filename = fields.String(
        required=True,
        validate=validate.Length(min=1, max=255),
        data_key="sourceFilename",
    )
    file_base64 = fields.String(
        required=True,
        validate=validate.Length(min=1, max=15_000_000),
        data_key="fileBase64",
    )


class ManualBulkUploadResultSchema(Schema):
    record_date = fields.Date(dump_only=True, data_key="recordDate")
    source_filename = fields.String(dump_only=True, data_key="sourceFilename")
    sheet_name = fields.String(dump_only=True, data_key="sheetName")
    row_count = fields.Integer(dump_only=True, data_key="rowCount")
    imported_count = fields.Integer(dump_only=True, data_key="importedCount")
    created_count = fields.Integer(dump_only=True, data_key="createdCount")
    updated_count = fields.Integer(dump_only=True, data_key="updatedCount")


class ManualImportRowSchema(Schema):
    user_id = fields.Integer(required=True, data_key="userId")
    record_date = fields.Date(required=True, data_key="date")
    production_count = fields.Integer(required=True, validate=validate.Range(min=0), data_key="productionCount")
    tech_issues_downtime_hours = _hour_field(required=True, data_key="techIssuesDowntimeHours")
    no_inventory_idle_time_hours = _hour_field(required=True, data_key="noInventoryIdleTimeHours")
    leave_hours = _hour_field(required=True, data_key="leaveHours")
    meeting_engagement_hours = _hour_field(required=True, data_key="meetingEngagementHours")


class ManualImportStartSchema(Schema):
    source_filename = fields.String(
        required=True, data_key="sourceFilename", validate=validate.Length(min=1, max=255)
    )
    file_checksum = fields.String(
        required=True, data_key="fileChecksum", validate=validate.Regexp(r"^[a-fA-F0-9]{64}$")
    )
    total_rows = fields.Integer(required=True, data_key="totalRows", validate=validate.Range(min=1))


class ManualImportChunkSchema(Schema):
    checksum = fields.String(required=True, validate=validate.Regexp(r"^[a-fA-F0-9]{64}$"))
    rows = fields.List(
        fields.Nested(ManualImportRowSchema), required=True, validate=validate.Length(min=1, max=2000)
    )


class ManualImportProgressSchema(Schema):
    id = fields.Integer(dump_only=True)
    status = fields.String(dump_only=True)
    source_filename = fields.String(dump_only=True, data_key="sourceFilename")
    total_rows = fields.Integer(dump_only=True, data_key="totalRows")
    processed_count = fields.Integer(dump_only=True, data_key="processedCount")
    created_count = fields.Integer(dump_only=True, data_key="createdCount")
    updated_count = fields.Integer(dump_only=True, data_key="updatedCount")
    unchanged_count = fields.Integer(dump_only=True, data_key="unchangedCount")
    uploaded_at = fields.DateTime(dump_only=True, data_key="uploadedAt")
    completed_at = fields.DateTime(dump_only=True, allow_none=True, data_key="completedAt")


ManualDailyRecordEnvelopeSchema = envelope_schema(
    "ManualDailyRecordEnvelopeSchema", fields.Nested(ManualDailyRecordSchema)
)
ManualDailyRecordListEnvelopeSchema = envelope_schema(
    "ManualDailyRecordListEnvelopeSchema", fields.List(fields.Nested(ManualDailyRecordSchema))
)
ManualBulkUploadEnvelopeSchema = envelope_schema(
    "ManualBulkUploadEnvelopeSchema", fields.Nested(ManualBulkUploadResultSchema)
)
ManualImportProgressEnvelopeSchema = envelope_schema(
    "ManualImportProgressEnvelopeSchema", fields.Nested(ManualImportProgressSchema)
)
