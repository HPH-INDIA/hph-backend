from marshmallow import Schema, ValidationError, fields, validate, validates_schema

from app.cohorts.models import VALID_PERIOD_SOURCES, Stage, UserStagePeriod
from app.extensions import db
from app.responses import envelope_schema


def _validate_stage_code(value):
    if db.session.get(Stage, value) is not None:
        return
    raise ValidationError(f"'{value}' is not a known stage code.")


class StageSchema(Schema):
    code = fields.String(dump_only=True)
    sort_order = fields.Integer(dump_only=True)
    duration_days = fields.Integer(dump_only=True, allow_none=True)


class CohortSchema(Schema):
    id = fields.Integer(dump_only=True)
    sequence_no = fields.Integer(dump_only=True)
    label = fields.String(validate=validate.Length(min=1, max=128), allow_none=True, load_default=None)
    window_start = fields.Date(required=True)
    window_end = fields.Date(allow_none=True, load_default=None)
    created_at = fields.DateTime(dump_only=True)
    member_count = fields.Method("get_member_count", dump_only=True)

    def get_member_count(self, cohort):
        return len(cohort.members)


class CoderSchema(Schema):
    id = fields.Integer(dump_only=True)
    full_name = fields.String(required=True, validate=validate.Length(min=1, max=255))
    join_date = fields.Date(required=True)
    # Set only by assign_cohort() (direct join) or a resolved cohort_join_review
    # (ambiguous join) - never accepted directly from a client.
    cohort_id = fields.Integer(dump_only=True, allow_none=True)


class CohortDetailSchema(CohortSchema):
    members = fields.List(fields.Nested(CoderSchema), dump_only=True)


class CoderStagePeriodSchema(Schema):
    id = fields.Integer(dump_only=True)
    stage_code = fields.String(dump_only=True)
    start_date = fields.Date(dump_only=True)
    end_date = fields.Date(dump_only=True, allow_none=True)
    source = fields.String(dump_only=True, validate=validate.OneOf(VALID_PERIOD_SOURCES))
    shifted_by_exception_days = fields.Integer(dump_only=True)


class DailyTargetQuerySchema(Schema):
    date = fields.Date(required=True)


class DailyTargetSchema(Schema):
    date = fields.Date(dump_only=True)
    stage_code = fields.String(dump_only=True, allow_none=True)
    daily_target = fields.Integer(dump_only=True, allow_none=True)


class StageTargetRuleSchema(Schema):
    recalculated_records = fields.Integer(dump_only=True, data_key="recalculatedRecords")
    apply_from = fields.String(dump_only=True, data_key="applyFrom")
    id = fields.Integer(dump_only=True)
    stage_code = fields.String(required=True, validate=_validate_stage_code)
    effective_from = fields.Date(required=True)
    effective_to = fields.Date(allow_none=True, load_default=None)
    daily_target = fields.Integer(required=True, validate=validate.Range(min=0))
    created_by_id = fields.Integer(dump_only=True)
    reason = fields.String(allow_none=True, load_default=None, validate=validate.Length(max=1000))
    created_at = fields.DateTime(dump_only=True)

    @validates_schema
    def validate_range(self, data, **kwargs):
        effective_to = data.get("effective_to")
        if effective_to is not None and "effective_from" in data and effective_to <= data["effective_from"]:
            raise ValidationError("effective_to must be after effective_from.", field_name="effective_to")


class StageTargetRuleQuerySchema(Schema):
    stage = fields.String(required=False, load_default=None)


class ChangeStageTargetSchema(Schema):
    stage_code = fields.String(required=True, validate=_validate_stage_code, data_key="stageCode")
    effective_from = fields.Date(load_default=None, data_key="effectiveFrom")
    apply_from = fields.String(load_default=None, validate=validate.OneOf(["today", "program_start"]), data_key="applyFrom")
    daily_target = fields.Integer(required=True, validate=validate.Range(min=0, max=2147483647), strict=True, data_key="dailyTarget")
    reason = fields.String(allow_none=True, load_default=None, validate=validate.Length(max=1000))


    @validates_schema
    def validate_scope(self, data, **kwargs):
        if (data.get("apply_from") is None) == (data.get("effective_from") is None):
            raise ValidationError("Choose applyFrom or effectiveFrom, but not both.")


class TeamCohortCreateSchema(Schema):
    label = fields.String(required=True, validate=validate.Length(min=1, max=128))
    window_start = fields.Date(required=True, data_key="windowStart")
    window_end = fields.Date(allow_none=True, load_default=None, data_key="windowEnd")
    member_ids = fields.List(
        fields.Integer(), required=True, validate=validate.Length(min=1), data_key="memberIds"
    )

    @validates_schema
    def validate_window(self, data, **kwargs):
        if data.get("window_end") is not None and data["window_end"] < data["window_start"]:
            raise ValidationError("windowEnd must be on or after windowStart.", field_name="windowEnd")


class TeamCoderOverviewQuerySchema(Schema):
    page = fields.Integer(load_default=1, validate=validate.Range(min=1))
    page_size = fields.Integer(load_default=25, validate=validate.Range(min=1, max=100), data_key="pageSize")
    cohort_id = fields.Integer(load_default=None, allow_none=True, data_key="cohortId")
    lead_id = fields.Integer(load_default=None, allow_none=True, validate=validate.Range(min=0), data_key="leadId")
    stage_code = fields.String(
        load_default=None,
        allow_none=True,
        validate=validate.OneOf(["Training", "M1", "M2", "M3", "M4", "Steady State", "Unassigned"]),
        data_key="stageCode",
    )


class CodingUserSummarySchema(Schema):
    id = fields.Integer(dump_only=True)
    email = fields.String(dump_only=True)
    first_name = fields.String(dump_only=True, data_key="firstName")
    last_name = fields.String(dump_only=True, data_key="lastName")
    emp_id = fields.String(dump_only=True, data_key="empId")
    role_type = fields.Method("get_role_type", dump_only=True, data_key="roleType")
    join_date = fields.Date(dump_only=True, allow_none=True, data_key="joinDate")
    is_active = fields.Boolean(dump_only=True, data_key="isActive")
    last_working_day = fields.Date(dump_only=True, allow_none=True, data_key="lastWorkingDay")

    def get_role_type(self, user):
        return user.role.role_type.code


class TeamCohortMemberSchema(Schema):
    user = fields.Nested(CodingUserSummarySchema, dump_only=True)
    joined_on = fields.Date(dump_only=True, data_key="joinedOn")
    current_stage = fields.Method("get_current_stage", dump_only=True, data_key="currentStage")

    def get_current_stage(self, membership):
        from app.cohorts.progression import business_today

        today = business_today()
        period = UserStagePeriod.query.filter(
            UserStagePeriod.user_id == membership.user_id,
            UserStagePeriod.start_date <= today,
            db.or_(UserStagePeriod.end_date.is_(None), UserStagePeriod.end_date >= today),
        ).first()
        return period.stage_code if period else None


class TeamCohortSchema(Schema):
    id = fields.Integer(dump_only=True)
    sequence_no = fields.Integer(dump_only=True, data_key="sequenceNo")
    label = fields.String(dump_only=True)
    window_start = fields.Date(dump_only=True, data_key="windowStart")
    window_end = fields.Date(dump_only=True, allow_none=True, data_key="windowEnd")
    created_at = fields.DateTime(dump_only=True, data_key="createdAt")
    member_count = fields.Method("get_member_count", dump_only=True, data_key="memberCount")
    members = fields.List(fields.Nested(TeamCohortMemberSchema), attribute="user_memberships", dump_only=True)

    def get_member_count(self, cohort):
        return len(cohort.user_memberships)


class TeamCoderCohortSummarySchema(Schema):
    id = fields.Integer(dump_only=True)
    label = fields.String(dump_only=True)


class StagePeriodSchema(Schema):
    stage_code = fields.String(dump_only=True, data_key="stageCode")
    start_date = fields.Date(dump_only=True, data_key="startDate")
    end_date = fields.Date(dump_only=True, allow_none=True, data_key="endDate")
    source = fields.String(dump_only=True)


class FoundationProgressSchema(Schema):
    eligibility = fields.String(dump_only=True)
    first_pvp_completed = fields.Date(dump_only=True, allow_none=True, data_key="firstPvpCompleted")
    first_foundation_completed = fields.Date(dump_only=True, allow_none=True, data_key="firstFoundationCompleted")
    current_stage = fields.String(dump_only=True, allow_none=True, data_key="currentStage")
    daily_target = fields.Integer(dump_only=True, allow_none=True, data_key="dailyTarget")
    periods = fields.List(fields.Nested(StagePeriodSchema), dump_only=True)


class TeamCoderOverviewItemSchema(Schema):
    joined_on = fields.Date(dump_only=True, allow_none=True, data_key="joinedOn")
    stage_as_of = fields.Date(dump_only=True, data_key="stageAsOf")
    first_completed = fields.Date(dump_only=True, allow_none=True, data_key="firstCompleted")
    refreshed_at = fields.DateTime(dump_only=True, allow_none=True, data_key="refreshedAt")
    periods = fields.List(fields.Nested(StagePeriodSchema), dump_only=True)
    foundation = fields.Nested(FoundationProgressSchema, dump_only=True)
    data_issue = fields.String(dump_only=True, allow_none=True, data_key="dataIssue")
    coder = fields.Nested(CodingUserSummarySchema, dump_only=True)
    cohort = fields.Nested(TeamCoderCohortSummarySchema, dump_only=True, allow_none=True)
    current_stage = fields.String(dump_only=True, allow_none=True, data_key="currentStage")
    daily_target = fields.Integer(dump_only=True, allow_none=True, data_key="dailyTarget")
    lead = fields.Nested(CodingUserSummarySchema, dump_only=True, allow_none=True)


class TeamCoderOverviewPageSchema(Schema):
    items = fields.List(fields.Nested(TeamCoderOverviewItemSchema), dump_only=True)
    page = fields.Integer(dump_only=True)
    page_size = fields.Integer(dump_only=True, data_key="pageSize")
    total = fields.Integer(dump_only=True)
    total_pages = fields.Integer(dump_only=True, data_key="totalPages")


class CohortJoinReviewSchema(Schema):
    id = fields.Integer(dump_only=True)
    coder_id = fields.Integer(dump_only=True)
    candidate_cohort_ids = fields.List(fields.Integer(), dump_only=True)
    status = fields.String(dump_only=True)
    resolved_cohort_id = fields.Integer(dump_only=True, allow_none=True)
    resolved_by_id = fields.Integer(dump_only=True, allow_none=True)
    resolved_at = fields.DateTime(dump_only=True, allow_none=True)
    created_at = fields.DateTime(dump_only=True)


class CohortJoinReviewQuerySchema(Schema):
    status = fields.String(required=False, load_default=None)


class ResolveCohortJoinReviewSchema(Schema):
    cohort_id = fields.Integer(required=True)


class StageExceptionSchema(Schema):
    id = fields.Integer(dump_only=True)
    coder_id = fields.Integer(dump_only=True)
    stage_code = fields.String(required=True, validate=_validate_stage_code)
    extra_days = fields.Integer(required=True, validate=validate.Range(min=1))
    reason = fields.String(required=True, validate=validate.Length(min=1))
    created_by_id = fields.Integer(dump_only=True)
    created_at = fields.DateTime(dump_only=True)


StageListEnvelopeSchema = envelope_schema("StageListEnvelopeSchema", fields.List(fields.Nested(StageSchema)))

CohortEnvelopeSchema = envelope_schema("CohortEnvelopeSchema", fields.Nested(CohortSchema))
CohortListEnvelopeSchema = envelope_schema("CohortListEnvelopeSchema", fields.List(fields.Nested(CohortSchema)))
CohortDetailEnvelopeSchema = envelope_schema("CohortDetailEnvelopeSchema", fields.Nested(CohortDetailSchema))

CoderEnvelopeSchema = envelope_schema("CoderEnvelopeSchema", fields.Nested(CoderSchema))
CoderListEnvelopeSchema = envelope_schema("CoderListEnvelopeSchema", fields.List(fields.Nested(CoderSchema)))

CoderStagePeriodListEnvelopeSchema = envelope_schema(
    "CoderStagePeriodListEnvelopeSchema", fields.List(fields.Nested(CoderStagePeriodSchema))
)
DailyTargetEnvelopeSchema = envelope_schema("DailyTargetEnvelopeSchema", fields.Nested(DailyTargetSchema))

StageTargetRuleEnvelopeSchema = envelope_schema(
    "StageTargetRuleEnvelopeSchema", fields.Nested(StageTargetRuleSchema)
)
StageTargetRuleListEnvelopeSchema = envelope_schema(
    "StageTargetRuleListEnvelopeSchema", fields.List(fields.Nested(StageTargetRuleSchema))
)
TeamCohortEnvelopeSchema = envelope_schema("TeamCohortEnvelopeSchema", fields.Nested(TeamCohortSchema))
TeamCohortListEnvelopeSchema = envelope_schema(
    "TeamCohortListEnvelopeSchema", fields.List(fields.Nested(TeamCohortSchema))
)
CodingUserSummaryListEnvelopeSchema = envelope_schema(
    "CodingUserSummaryListEnvelopeSchema", fields.List(fields.Nested(CodingUserSummarySchema))
)
TeamCoderOverviewEnvelopeSchema = envelope_schema(
    "TeamCoderOverviewEnvelopeSchema", fields.Nested(TeamCoderOverviewPageSchema)
)

CohortJoinReviewEnvelopeSchema = envelope_schema(
    "CohortJoinReviewEnvelopeSchema", fields.Nested(CohortJoinReviewSchema)
)
CohortJoinReviewListEnvelopeSchema = envelope_schema(
    "CohortJoinReviewListEnvelopeSchema", fields.List(fields.Nested(CohortJoinReviewSchema))
)

StageExceptionEnvelopeSchema = envelope_schema(
    "StageExceptionEnvelopeSchema", fields.Nested(StageExceptionSchema)
)
StageExceptionListEnvelopeSchema = envelope_schema(
    "StageExceptionListEnvelopeSchema", fields.List(fields.Nested(StageExceptionSchema))
)


class ChangeFoundationTargetSchema(ChangeStageTargetSchema):
    stage_code = fields.String(required=True, data_key="stageCode",
                               validate=validate.OneOf(["W1", "W2", "W3", "W4", "Steady State"]))
