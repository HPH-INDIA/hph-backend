from marshmallow import Schema, ValidationError, fields, validate, validates_schema

from app.responses import envelope_schema
from app.kairon.schemas import KaironChartRecordSchema
from app.manual_daily_records.schemas import ManualDailyRecordQuerySchema, ManualDailyRecordSchema


class PaginationQuerySchema(Schema):
    page = fields.Integer(required=False, load_default=1, validate=validate.Range(min=1))
    page_size = fields.Integer(
        required=False, load_default=25, validate=validate.Range(min=1, max=100), data_key="pageSize"
    )


class SelfKaironChartQuerySchema(PaginationQuerySchema):
    """GET /api/reports/kairon query args. Deliberately has no user_id/
    user_ids field at all (unlike KaironChartQuerySchema) - this endpoint
    is always scoped to the caller server-side, so there's no query
    parameter that could widen it to someone else's charts.
    """

    status = fields.String(required=False, load_default=None)
    level = fields.String(required=False, load_default=None)
    as_of_date = fields.Date(required=False, load_default=None, data_key="asOfDate")


class KaironCompletedUserQuerySchema(PaginationQuerySchema):
    completed_date = fields.Date(required=True, data_key="completedDate")
    analyst = fields.String(
        required=False,
        load_default=None,
        validate=validate.Length(min=1, max=255),
    )


class KaironCompletedRecordQuerySchema(PaginationQuerySchema):
    completed_date = fields.Date(required=True, data_key="completedDate")
    user_id = fields.Integer(required=True, data_key="userId")


class SelfManualRecordsQuerySchema(PaginationQuerySchema):
    pass


class ManualReviewQuerySchema(ManualDailyRecordQuerySchema, PaginationQuerySchema):
    pass


class KaironRecordPageSchema(Schema):
    items = fields.List(fields.Nested(KaironChartRecordSchema), dump_only=True)
    page = fields.Integer(dump_only=True)
    page_size = fields.Integer(dump_only=True, data_key="pageSize")
    total = fields.Integer(dump_only=True)
    total_pages = fields.Integer(dump_only=True, data_key="totalPages")


class KaironCompletedDailyCountSchema(Schema):
    date = fields.Date(dump_only=True)
    count = fields.Integer(dump_only=True)


class KaironCompletedDailyCountPageSchema(Schema):
    items = fields.List(fields.Nested(KaironCompletedDailyCountSchema), dump_only=True)
    page = fields.Integer(dump_only=True)
    page_size = fields.Integer(dump_only=True, data_key="pageSize")
    total = fields.Integer(dump_only=True)
    total_pages = fields.Integer(dump_only=True, data_key="totalPages")


class KaironCompletedUserSchema(Schema):
    user_id = fields.Integer(dump_only=True, data_key="userId")
    first_name = fields.String(dump_only=True, data_key="firstName")
    last_name = fields.String(dump_only=True, data_key="lastName")
    count = fields.Integer(dump_only=True)


class KaironCompletedUserPageSchema(Schema):
    items = fields.List(fields.Nested(KaironCompletedUserSchema), dump_only=True)
    page = fields.Integer(dump_only=True)
    page_size = fields.Integer(dump_only=True, data_key="pageSize")
    total = fields.Integer(dump_only=True)
    total_pages = fields.Integer(dump_only=True, data_key="totalPages")


class ManualRecordPageSchema(Schema):
    items = fields.List(fields.Nested(ManualDailyRecordSchema), dump_only=True)
    page = fields.Integer(dump_only=True)
    page_size = fields.Integer(dump_only=True, data_key="pageSize")
    total = fields.Integer(dump_only=True)
    total_pages = fields.Integer(dump_only=True, data_key="totalPages")


class ManualTeamDayQuerySchema(Schema):
    date = fields.Date(required=True)


class ManualTeamRangeQuerySchema(Schema):
    from_date = fields.Date(required=True, data_key="fromDate")
    to_date = fields.Date(required=True, data_key="toDate")

    @validates_schema
    def validate_range(self, data, **kwargs):
        if data["from_date"] > data["to_date"]:
            raise ValidationError("To date must be on or after from date.", field_name="toDate")


class KaironTeamRecordQuerySchema(PaginationQuerySchema, ManualTeamRangeQuerySchema):
    user_id = fields.Integer(required=True, validate=validate.Range(min=1), data_key="userId")


class ManualTeamMemberSchema(Schema):
    id = fields.Integer(dump_only=True)
    first_name = fields.String(dump_only=True, data_key="firstName")
    last_name = fields.String(dump_only=True, data_key="lastName")
    emp_id = fields.String(dump_only=True, data_key="empId")


class KaironChartSummarySchema(Schema):
    pvp = fields.Integer(dump_only=True)
    foundation = fields.Integer(dump_only=True)
    on_hold = fields.Integer(dump_only=True, data_key="onHold")
    total = fields.Integer(dump_only=True)


class KaironTeamCoderSchema(Schema):
    user = fields.Nested(ManualTeamMemberSchema, dump_only=True)
    days = fields.List(fields.Nested(KaironCompletedDailyCountSchema), dump_only=True)
    count = fields.Integer(dump_only=True)
    summary = fields.Nested(KaironChartSummarySchema, dump_only=True)


class KaironLeadTeamRangeSchema(Schema):
    from_date = fields.Date(dump_only=True, data_key="fromDate")
    to_date = fields.Date(dump_only=True, data_key="toDate")
    lead = fields.Nested(ManualTeamMemberSchema, dump_only=True)
    lead_days = fields.List(fields.Nested(KaironCompletedDailyCountSchema), dump_only=True, data_key="leadDays")
    lead_summary = fields.Nested(KaironChartSummarySchema, dump_only=True, data_key="leadSummary")
    coders = fields.List(fields.Nested(KaironTeamCoderSchema), dump_only=True)


KaironLeadTeamRangeEnvelopeSchema = envelope_schema(
    "KaironLeadTeamRangeEnvelopeSchema", fields.Nested(KaironLeadTeamRangeSchema)
)


class KaironManagerTeamCoderSchema(Schema):
    user = fields.Nested(ManualTeamMemberSchema, dump_only=True)
    summary = fields.Nested(KaironChartSummarySchema, dump_only=True)


class KaironManagerTeamSchema(Schema):
    lead = fields.Nested(ManualTeamMemberSchema, dump_only=True, allow_none=True)
    lead_summary = fields.Nested(KaironChartSummarySchema, dump_only=True, data_key="leadSummary")
    coders = fields.List(fields.Nested(KaironManagerTeamCoderSchema), dump_only=True)


class KaironManagerTeamRangeSchema(Schema):
    from_date = fields.Date(dump_only=True, data_key="fromDate")
    to_date = fields.Date(dump_only=True, data_key="toDate")
    teams = fields.List(fields.Nested(KaironManagerTeamSchema), dump_only=True)


KaironManagerTeamRangeEnvelopeSchema = envelope_schema(
    "KaironManagerTeamRangeEnvelopeSchema", fields.Nested(KaironManagerTeamRangeSchema)
)


class ManualTeamCoderSchema(Schema):
    user = fields.Nested(ManualTeamMemberSchema, dump_only=True)
    record = fields.Nested(ManualDailyRecordSchema, dump_only=True, allow_none=True)


class ManualTeamSchema(Schema):
    lead = fields.Nested(ManualTeamMemberSchema, dump_only=True, allow_none=True)
    lead_record = fields.Nested(ManualDailyRecordSchema, dump_only=True, allow_none=True, data_key="leadRecord")
    coders = fields.List(fields.Nested(ManualTeamCoderSchema), dump_only=True)


class ManualTeamDaySchema(Schema):
    date = fields.Date(dump_only=True)
    teams = fields.List(fields.Nested(ManualTeamSchema), dump_only=True)


ManualTeamDayEnvelopeSchema = envelope_schema(
    "ManualTeamDayEnvelopeSchema", fields.Nested(ManualTeamDaySchema)
)


class ManualTeamRangeCoderSchema(Schema):
    user = fields.Nested(ManualTeamMemberSchema, dump_only=True)
    records = fields.List(fields.Nested(ManualDailyRecordSchema), dump_only=True)


class ManualTeamRangeGroupSchema(Schema):
    lead = fields.Nested(ManualTeamMemberSchema, dump_only=True, allow_none=True)
    lead_records = fields.List(fields.Nested(ManualDailyRecordSchema), dump_only=True, data_key="leadRecords")
    coders = fields.List(fields.Nested(ManualTeamRangeCoderSchema), dump_only=True)


class ManualTeamRangeSchema(Schema):
    from_date = fields.Date(dump_only=True, data_key="fromDate")
    to_date = fields.Date(dump_only=True, data_key="toDate")
    teams = fields.List(fields.Nested(ManualTeamRangeGroupSchema), dump_only=True)


ManualTeamRangeEnvelopeSchema = envelope_schema(
    "ManualTeamRangeEnvelopeSchema", fields.Nested(ManualTeamRangeSchema)
)


class BulkApproveManualDailyRecordsSchema(Schema):
    ids = fields.List(fields.Integer(), required=True, validate=validate.Length(min=1))


class BulkRejectItemSchema(Schema):
    id = fields.Integer(required=True)
    reason = fields.String(required=False, load_default=None, allow_none=True)


class BulkRejectManualDailyRecordsSchema(Schema):
    # Each item carries its reason, which may be shared across the batch.
    items = fields.List(fields.Nested(BulkRejectItemSchema), required=True, validate=validate.Length(min=1))


class BulkReviewSkipSchema(Schema):
    id = fields.Integer(dump_only=True)
    reason = fields.String(dump_only=True)


class BulkApproveResultSchema(Schema):
    approved = fields.List(fields.Integer(), dump_only=True)
    skipped = fields.List(fields.Nested(BulkReviewSkipSchema), dump_only=True)


class BulkRejectResultSchema(Schema):
    rejected = fields.List(fields.Integer(), dump_only=True)
    skipped = fields.List(fields.Nested(BulkReviewSkipSchema), dump_only=True)


class CodingDashboardQuerySchema(Schema):
    """GET /api/dashboards/coding query args - from/to (a custom range or,
    when equal, a single day), or the date/month shorthands. See
    services.resolve_dashboard_window() for how these combine and the
    month-to-date default when none are given.
    """

    from_date = fields.Date(required=False, load_default=None, data_key="from")
    to_date = fields.Date(required=False, load_default=None, data_key="to")
    date = fields.Date(required=False, load_default=None)
    month = fields.String(required=False, load_default=None, validate=validate.Regexp(r"^\d{4}-\d{2}$"))
    year = fields.Integer(required=False, load_default=None, validate=validate.Range(min=2000, max=2100))
    program = fields.String(
        required=False,
        load_default=None,
        validate=validate.OneOf(["PVP", "FOUNDATION"]),
    )
    lead_id = fields.Integer(required=False, load_default=None, data_key="leadId")
    cohort_id = fields.Integer(required=False, load_default=None, data_key="cohortId")
    include_daily = fields.Boolean(required=False, load_default=False, data_key="includeDaily")


class EfficiencyQuerySchema(Schema):
    from_date = fields.Date(required=False, load_default=None, data_key="from")
    to_date = fields.Date(required=False, load_default=None, data_key="to")
    date = fields.Date(required=False, load_default=None)
    month = fields.String(required=False, load_default=None, validate=validate.Regexp(r"^\d{4}-\d{2}$"))
    year = fields.Integer(required=False, load_default=None, validate=validate.Range(min=2000, max=2100))


class MonthlyGoalQuerySchema(Schema):
    month = fields.String(required=False, load_default=None, validate=validate.Regexp(r"^\d{4}-\d{2}$"))


class MonthlyGoalUserSchema(Schema):
    user_id = fields.Integer(dump_only=True, data_key="userId")
    name = fields.String(dump_only=True)
    manual_charts = fields.Integer(dump_only=True, data_key="manualCharts")
    completed_charts = fields.Integer(dump_only=True, data_key="completedCharts")
    target_charts = fields.Integer(dump_only=True, data_key="targetCharts")
    adjusted_target_charts = fields.Decimal(
        dump_only=True, as_string=True, places=2, data_key="adjustedTargetCharts"
    )
    adjusted_difference = fields.Decimal(dump_only=True, as_string=True, places=2, data_key="adjustedDifference")
    difference = fields.Integer(dump_only=True)


class MonthlyGoalSchema(Schema):
    manual_charts = fields.Integer(dump_only=True, data_key="manualCharts")
    users = fields.List(fields.Nested(MonthlyGoalUserSchema), dump_only=True)
    month = fields.String(dump_only=True)
    scope = fields.String(dump_only=True)
    user_count = fields.Integer(dump_only=True, data_key="userCount")
    completed_charts = fields.Integer(dump_only=True, data_key="completedCharts")
    target_charts = fields.Integer(dump_only=True, data_key="targetCharts")
    adjusted_target_charts = fields.Decimal(
        dump_only=True, as_string=True, places=2, data_key="adjustedTargetCharts"
    )
    adjusted_difference = fields.Decimal(dump_only=True, as_string=True, places=2, data_key="adjustedDifference")
    difference = fields.Integer(dump_only=True)
    calendar_working_days = fields.Integer(dump_only=True, data_key="calendarWorkingDays")
    eligible_days = fields.Integer(dump_only=True, data_key="eligibleDays")
    holiday_count = fields.Integer(dump_only=True, data_key="holidayCount")
    leave_days_excluded = fields.Integer(dump_only=True, data_key="leaveDaysExcluded")


class CodingDashboardKaironSchema(Schema):
    active = fields.Integer(dump_only=True)
    on_hold = fields.Integer(dump_only=True, data_key="onHold")
    completed = fields.Integer(dump_only=True)


class CodingDashboardManualSchema(Schema):
    production_count = fields.Integer(dump_only=True, data_key="productionCount")
    pvp_count = fields.Integer(dump_only=True, data_key="pvpCount")
    foundation_count = fields.Integer(dump_only=True, data_key="foundationCount")
    tech_issues_downtime_hours = fields.Decimal(dump_only=True, as_string=True, data_key="techIssuesDowntimeHours")
    no_inventory_idle_time_hours = fields.Decimal(
        dump_only=True, as_string=True, data_key="noInventoryIdleTimeHours"
    )
    leave_hours = fields.Decimal(dump_only=True, as_string=True, data_key="leaveHours")
    meeting_engagement_hours = fields.Decimal(dump_only=True, as_string=True, data_key="meetingEngagementHours")
    pending_count = fields.Integer(dump_only=True, data_key="pendingCount")
    record_count = fields.Integer(dump_only=True, data_key="recordCount")


class DailyEfficiencySchema(Schema):
    date = fields.Date(dump_only=True)
    stage = fields.String(dump_only=True, allow_none=True)
    daily_target = fields.Integer(dump_only=True, allow_none=True, data_key="dailyTarget")
    manual_charts = fields.Integer(dump_only=True, data_key="manualCharts")
    kairon_charts = fields.Integer(dump_only=True, data_key="kaironCharts")
    inside_minutes = fields.Integer(dump_only=True, allow_none=True, data_key="insideMinutes")
    downtime_minutes = fields.Integer(dump_only=True, data_key="downtimeMinutes")
    idle_minutes = fields.Integer(dump_only=True, data_key="idleMinutes")
    leave_minutes = fields.Integer(dump_only=True, data_key="leaveMinutes")
    meeting_minutes = fields.Integer(dump_only=True, data_key="meetingMinutes")
    excluded_minutes = fields.Integer(dump_only=True, data_key="excludedMinutes")
    productive_minutes = fields.Integer(dump_only=True, allow_none=True, data_key="productiveMinutes")
    target_minutes = fields.Integer(dump_only=True, allow_none=True, data_key="targetMinutes")
    adjusted_target = fields.Decimal(dump_only=True, allow_none=True, as_string=True, data_key="adjustedTarget")
    adjusted_cpd = fields.Decimal(
        dump_only=True, allow_none=True, as_string=True, places=2, data_key="adjustedCpd"
    )
    manual_efficiency_percent = fields.Decimal(
        dump_only=True, allow_none=True, as_string=True, data_key="manualEfficiencyPercent"
    )
    kairon_efficiency_percent = fields.Decimal(
        dump_only=True, allow_none=True, as_string=True, data_key="kaironEfficiencyPercent"
    )
    manual_cpd = fields.Decimal(dump_only=True, allow_none=True, as_string=True, data_key="manualCpd")
    kairon_cpd = fields.Decimal(dump_only=True, allow_none=True, as_string=True, data_key="kaironCpd")
    target_cpd = fields.Decimal(dump_only=True, allow_none=True, as_string=True, data_key="targetCpd")
    manual_status = fields.String(dump_only=True, allow_none=True, data_key="manualStatus")


class EfficiencySummarySchema(Schema):
    from_date = fields.Date(dump_only=True, data_key="from")
    to_date = fields.Date(dump_only=True, data_key="to")
    manual_charts = fields.Integer(dump_only=True, data_key="manualCharts")
    kairon_charts = fields.Integer(dump_only=True, data_key="kaironCharts")
    adjusted_target = fields.Decimal(dump_only=True, as_string=True, data_key="adjustedTarget")
    inside_minutes = fields.Integer(dump_only=True, data_key="insideMinutes")
    login_days = fields.Integer(dump_only=True, data_key="loginDays")
    productive_minutes = fields.Integer(dump_only=True, data_key="productiveMinutes")
    target_minutes = fields.Integer(dump_only=True, data_key="targetMinutes")
    calculated_days = fields.Integer(dump_only=True, data_key="calculatedDays")
    manual_efficiency_percent = fields.Decimal(
        dump_only=True, allow_none=True, as_string=True, data_key="manualEfficiencyPercent"
    )
    kairon_efficiency_percent = fields.Decimal(
        dump_only=True, allow_none=True, as_string=True, data_key="kaironEfficiencyPercent"
    )
    manual_cpd = fields.Decimal(dump_only=True, allow_none=True, as_string=True, data_key="manualCpd")
    kairon_cpd = fields.Decimal(dump_only=True, allow_none=True, as_string=True, data_key="kaironCpd")
    target_cpd = fields.Decimal(dump_only=True, allow_none=True, as_string=True, data_key="targetCpd")
    daily = fields.List(fields.Nested(DailyEfficiencySchema), dump_only=True)


class CodingDashboardCardSchema(Schema):
    user_id = fields.Integer(dump_only=True, data_key="userId")
    first_name = fields.String(dump_only=True, data_key="firstName")
    last_name = fields.String(dump_only=True, data_key="lastName")
    email = fields.String(dump_only=True)
    is_active = fields.Boolean(dump_only=True, data_key="isActive")
    last_working_day = fields.Date(dump_only=True, allow_none=True, data_key="lastWorkingDay")
    lead_id = fields.Integer(dump_only=True, allow_none=True, data_key="leadId")
    kairon = fields.Nested(CodingDashboardKaironSchema, dump_only=True)
    manual = fields.Nested(CodingDashboardManualSchema, dump_only=True)
    efficiency = fields.Nested(EfficiencySummarySchema, dump_only=True)


BulkApproveResultEnvelopeSchema = envelope_schema(
    "BulkApproveResultEnvelopeSchema", fields.Nested(BulkApproveResultSchema)
)
BulkRejectResultEnvelopeSchema = envelope_schema(
    "BulkRejectResultEnvelopeSchema", fields.Nested(BulkRejectResultSchema)
)
CodingDashboardEnvelopeSchema = envelope_schema(
    "CodingDashboardEnvelopeSchema", fields.List(fields.Nested(CodingDashboardCardSchema))
)
EfficiencySummaryEnvelopeSchema = envelope_schema(
    "EfficiencySummaryEnvelopeSchema", fields.Nested(EfficiencySummarySchema)
)
MonthlyGoalEnvelopeSchema = envelope_schema(
    "MonthlyGoalEnvelopeSchema", fields.Nested(MonthlyGoalSchema)
)
KaironRecordPageEnvelopeSchema = envelope_schema(
    "KaironRecordPageEnvelopeSchema", fields.Nested(KaironRecordPageSchema)
)
ManualRecordPageEnvelopeSchema = envelope_schema(
    "ManualRecordPageEnvelopeSchema", fields.Nested(ManualRecordPageSchema)
)
KaironCompletedDailyCountPageEnvelopeSchema = envelope_schema(
    "KaironCompletedDailyCountPageEnvelopeSchema",
    fields.Nested(KaironCompletedDailyCountPageSchema),
)
KaironCompletedUserPageEnvelopeSchema = envelope_schema(
    "KaironCompletedUserPageEnvelopeSchema",
    fields.Nested(KaironCompletedUserPageSchema),
)
