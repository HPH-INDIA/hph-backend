from marshmallow import Schema, fields, validate

from app.practices.models import ACCESS_STATUSES, PRACTICE_STATUSES
from app.responses import envelope_schema


def _str(max_len, **kwargs):
    return fields.String(allow_none=True, validate=validate.Length(max=max_len), **kwargs)


class PracticeWriteSchema(Schema):
    name = fields.String(validate=validate.Length(min=1, max=255))
    tin = _str(32)
    worktype = _str(64)
    priority_rank = fields.Integer(allow_none=True, data_key="priorityRank")
    cms_count = fields.Integer(allow_none=True, data_key="cmsCount", validate=validate.Range(min=0))
    emr = _str(255)
    affiliation = _str(255)
    pipeline_2027 = _str(128, data_key="pipeline2027")
    filter_72 = _str(16, data_key="filter72")
    onshore_coders = _str(500, data_key="onshoreCoders")
    notes = fields.String(allow_none=True)


class PracticeCreateSchema(PracticeWriteSchema):
    name = fields.String(required=True, validate=validate.Length(min=1, max=255))
    # The Add practice form lets the manager pick the starting status.
    status = fields.String(load_default="Active", validate=validate.OneOf(PRACTICE_STATUSES))


class PracticeListQuerySchema(Schema):
    status = fields.List(fields.String(validate=validate.OneOf(PRACTICE_STATUSES)), load_default=None)
    worktype = fields.String(load_default=None)
    search = fields.String(load_default=None)
    user_id = fields.List(fields.Integer(), data_key="userId", load_default=None)
    access_status = fields.List(fields.String(validate=validate.OneOf(ACCESS_STATUSES)), data_key="accessStatus",
                                load_default=None)
    page = fields.Integer(load_default=1, validate=validate.Range(min=1))
    page_size = fields.Integer(load_default=25, data_key="pageSize", validate=validate.Range(min=1, max=5000))


class PracticeStatusSchema(Schema):
    status = fields.String(required=True, validate=validate.OneOf(PRACTICE_STATUSES))
    effective_from = fields.Date(load_default=None, allow_none=True, data_key="effectiveFrom")
    reason = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=500))


class AssignmentCreateSchema(Schema):
    user_id = fields.Integer(required=True, data_key="userId")
    access_status = fields.String(load_default=None, allow_none=True, data_key="accessStatus",
                                  validate=validate.OneOf(ACCESS_STATUSES))
    access_status_since = fields.Date(load_default=None, allow_none=True, data_key="accessStatusSince")


class AssignmentUpdateSchema(Schema):
    access_status = fields.String(allow_none=True, data_key="accessStatus", validate=validate.OneOf(ACCESS_STATUSES))
    access_status_since = fields.Date(allow_none=True, data_key="accessStatusSince")


class PracticeRequestCreateSchema(Schema):
    name = fields.String(required=True, validate=validate.Length(min=1, max=255))
    tin = _str(32, load_default=None)
    note = _str(1000, load_default=None)


class PracticeRequestQuerySchema(Schema):
    status = fields.String(load_default="pending", validate=validate.OneOf(("pending", "assigned", "created", "rejected", "all")))


class PracticeRequestResolveSchema(Schema):
    action = fields.String(required=True, validate=validate.OneOf(("assign", "create", "reject")))
    practice_id = fields.Integer(load_default=None, allow_none=True, data_key="practiceId")
    practice = fields.Nested(PracticeCreateSchema, load_default=None, allow_none=True)
    note = _str(1000, load_default=None)


class PracticeImportRowSchema(Schema):
    class Meta:
        unknown = "exclude"

    worktype = fields.Raw(load_default=None, allow_none=True)
    pipeline_2027 = fields.Raw(load_default=None, allow_none=True, data_key="pipeline2027")
    priority_rank = fields.Raw(load_default=None, allow_none=True, data_key="priorityRank")
    tin = fields.Raw(load_default=None, allow_none=True)
    practice = fields.Raw(load_default=None, allow_none=True)
    cms = fields.Raw(load_default=None, allow_none=True)
    affiliation = fields.Raw(load_default=None, allow_none=True)
    coder_1lr = fields.Raw(load_default=None, allow_none=True, data_key="coder1lr")
    qa_2lr = fields.Raw(load_default=None, allow_none=True, data_key="qa2lr")
    onshore_coders = fields.Raw(load_default=None, allow_none=True, data_key="onshoreCoders")
    emr = fields.Raw(load_default=None, allow_none=True)
    coder_1lr_access = fields.Raw(load_default=None, allow_none=True, data_key="coder1lrAccess")
    qa_2lr_access = fields.Raw(load_default=None, allow_none=True, data_key="qa2lrAccess")
    filter_72 = fields.Raw(load_default=None, allow_none=True, data_key="filter72")
    notes = fields.Raw(load_default=None, allow_none=True)


class PracticeImportSchema(Schema):
    rows = fields.List(fields.Nested(PracticeImportRowSchema), required=True, validate=validate.Length(min=1, max=5000))
    user_mappings = fields.Dict(keys=fields.String(), values=fields.Integer(), load_default=dict, data_key="userMappings")


DataEnvelopeSchema = envelope_schema("PracticesDataEnvelopeSchema", fields.Raw(allow_none=True))
