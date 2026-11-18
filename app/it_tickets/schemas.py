from marshmallow import Schema, fields, validate

from app.it_tickets.models import TICKET_STATUSES
from app.it_tickets.services import DELETE_REASONS
from app.practices.models import ACCESS_STATUSES
from app.responses import envelope_schema


class TicketQuerySchema(Schema):
    scope = fields.String(load_default="all", validate=validate.OneOf(("mine", "all")))
    user_id = fields.List(fields.Integer(), data_key="userId", load_default=None)
    role = fields.String(load_default="all", validate=validate.OneOf(("coder", "qa", "all")))
    status = fields.List(fields.String(validate=validate.OneOf(TICKET_STATUSES)), load_default=None)
    access_status = fields.List(fields.String(validate=validate.OneOf(ACCESS_STATUSES)), data_key="accessStatus",
                                load_default=None)
    practice_id = fields.Integer(data_key="practiceId", load_default=None)
    category_id = fields.Integer(data_key="categoryId", load_default=None)
    opened_from = fields.Date(data_key="openedFrom", load_default=None)
    opened_to = fields.Date(data_key="openedTo", load_default=None)
    search = fields.String(load_default=None)
    sort = fields.String(load_default="oldest", validate=validate.OneOf(("oldest", "newest", "aging")))
    page = fields.Integer(load_default=1, validate=validate.Range(min=1))
    page_size = fields.Integer(load_default=25, data_key="pageSize", validate=validate.Range(min=1, max=5000))


class IdleTimeQuerySchema(TicketQuerySchema):
    """Ticket filters plus the record-date window and grouping for idle hours lost to tickets."""
    idle_from = fields.Date(data_key="idleFrom", load_default=None)
    idle_to = fields.Date(data_key="idleTo", load_default=None)
    group = fields.String(load_default="day", validate=validate.OneOf(("day", "week")))


class TicketCreateSchema(Schema):
    practice_id = fields.Integer(required=True, data_key="practiceId")
    jira_key = fields.String(required=True, data_key="jiraKey", validate=validate.Length(min=1, max=32))
    issue_category_id = fields.Integer(required=True, data_key="issueCategoryId")
    opened_on = fields.Date(load_default=None, data_key="openedOn")
    hph_status_note = fields.String(load_default=None, allow_none=True, data_key="hphStatusNote",
                                    validate=validate.Length(max=4000))
    charts_available = fields.Integer(load_default=None, allow_none=True, data_key="chartsAvailable",
                                      validate=validate.Range(min=0))
    not_in_kairon = fields.Boolean(load_default=False, data_key="notInKairon")


class TicketUpdateSchema(Schema):
    issue_category_id = fields.Integer(data_key="issueCategoryId")
    hph_status_note = fields.String(allow_none=True, data_key="hphStatusNote", validate=validate.Length(max=4000))
    charts_available = fields.Integer(allow_none=True, data_key="chartsAvailable", validate=validate.Range(min=0))
    not_in_kairon = fields.Boolean(data_key="notInKairon")
    follow_up_issue = fields.String(allow_none=True, data_key="followUpIssue", validate=validate.Length(max=4000))
    status = fields.String(validate=validate.OneOf(TICKET_STATUSES))
    resolved_on = fields.Date(allow_none=True, data_key="resolvedOn")
    opened_on = fields.Date(data_key="openedOn")


class TicketDeleteSchema(Schema):
    reason = fields.String(required=True, validate=validate.OneOf(DELETE_REASONS))
    note = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=500))


class CategoryQuerySchema(Schema):
    include_retired = fields.Boolean(load_default=False, data_key="includeRetired")


class CategoryCreateSchema(Schema):
    name = fields.String(required=True, validate=validate.Length(min=1, max=128))


class CategoryUpdateSchema(Schema):
    name = fields.String(validate=validate.Length(min=1, max=128))
    active = fields.Boolean()


class TicketImportRowSchema(Schema):
    class Meta:
        unknown = "exclude"

    coder_name = fields.Raw(load_default=None, allow_none=True, data_key="coderName")
    role = fields.Raw(load_default=None, allow_none=True)
    practice_name = fields.Raw(load_default=None, allow_none=True, data_key="practiceName")
    issue_category = fields.Raw(load_default=None, allow_none=True, data_key="issueCategory")
    hph_status = fields.Raw(load_default=None, allow_none=True, data_key="hphStatus")
    ticket_id = fields.Raw(load_default=None, allow_none=True, data_key="ticketId")
    ticket_open_date = fields.Raw(load_default=None, allow_none=True, data_key="ticketOpenDate")
    ticket_status = fields.Raw(load_default=None, allow_none=True, data_key="ticketStatus")
    ticket_resolved_date = fields.Raw(load_default=None, allow_none=True, data_key="ticketResolvedDate")
    charts_available = fields.Raw(load_default=None, allow_none=True, data_key="chartsAvailable")
    follow_up_issue = fields.Raw(load_default=None, allow_none=True, data_key="followUpIssue")


class TicketImportSchema(Schema):
    rows = fields.List(fields.Nested(TicketImportRowSchema), required=True, validate=validate.Length(min=1, max=5000))
    user_mappings = fields.Dict(keys=fields.String(), values=fields.Integer(), load_default=dict, data_key="userMappings")
    practice_mappings = fields.Dict(keys=fields.String(), values=fields.Integer(), load_default=dict, data_key="practiceMappings")


DataEnvelopeSchema = envelope_schema("ItTicketsDataEnvelopeSchema", fields.Raw(allow_none=True))
