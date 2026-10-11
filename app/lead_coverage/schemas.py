from flask import g
from marshmallow import Schema, fields, validate

from app.auth import has_feature
from app.lead_coverage.services import can_cancel, can_decide, is_active_coverage
from app.responses import envelope_schema


class LeadCoverageCreateSchema(Schema):
    source_lead_id = fields.Integer(data_key="sourceLeadId", validate=validate.Range(min=1))
    covering_lead_id = fields.Integer(required=True, data_key="coveringLeadId", validate=validate.Range(min=1))
    starts_on = fields.Date(required=True, data_key="startsOn")
    ends_on = fields.Date(required=True, data_key="endsOn")
    reason = fields.String(load_default=None, allow_none=True, validate=validate.Length(max=2000))


class CoverageLeadSchema(Schema):
    id = fields.Integer()
    first_name = fields.String(data_key="firstName")
    last_name = fields.String(data_key="lastName")
    emp_id = fields.String(data_key="empId")
    project_id = fields.Integer(data_key="projectId")
    reports_to_id = fields.Integer(data_key="managerId")


class LeadCoverageSchema(Schema):
    id = fields.Integer()
    source_lead_id = fields.Integer(data_key="sourceLeadId")
    covering_lead_id = fields.Integer(data_key="coveringLeadId")
    source_lead = fields.Nested(CoverageLeadSchema, data_key="sourceLead")
    covering_lead = fields.Nested(CoverageLeadSchema, data_key="coveringLead")
    starts_on = fields.Date(data_key="startsOn")
    ends_on = fields.Date(data_key="endsOn")
    reason = fields.String(allow_none=True)
    status = fields.String()
    created_by_id = fields.Integer(data_key="createdById")
    decided_by_id = fields.Integer(data_key="decidedById", allow_none=True)
    decided_at = fields.DateTime(data_key="decidedAt", allow_none=True)
    cancelled_by_id = fields.Integer(data_key="cancelledById", allow_none=True)
    cancelled_at = fields.DateTime(data_key="cancelledAt", allow_none=True)
    created_at = fields.DateTime(data_key="createdAt")
    is_active = fields.Method("get_is_active", data_key="isActive")
    can_approve = fields.Method("get_can_decide", data_key="canApprove")
    can_reject = fields.Method("get_can_decide", data_key="canReject")
    can_cancel = fields.Method("get_can_cancel", data_key="canCancel")

    def get_is_active(self, record):
        return is_active_coverage(record)

    def get_can_decide(self, record):
        return can_decide(record, g.user)

    def get_can_cancel(self, record):
        return can_cancel(record, g.user)


class CoverageLeadOptionSchema(CoverageLeadSchema):
    can_cover = fields.Method("get_can_cover", data_key="canCover")

    def get_can_cover(self, lead):
        return has_feature(lead, "reports", "write")


class LeadCoverageOptionsSchema(Schema):
    leads = fields.List(fields.Nested(CoverageLeadOptionSchema))
    today = fields.Date()
    timezone = fields.String()


LeadCoverageEnvelopeSchema = envelope_schema("LeadCoverageEnvelopeSchema", fields.Nested(LeadCoverageSchema))
LeadCoverageListEnvelopeSchema = envelope_schema("LeadCoverageListEnvelopeSchema", fields.List(fields.Nested(LeadCoverageSchema)))
LeadCoverageOptionsEnvelopeSchema = envelope_schema("LeadCoverageOptionsEnvelopeSchema", fields.Nested(LeadCoverageOptionsSchema))
