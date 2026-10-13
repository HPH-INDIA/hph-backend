from flask import g, request
from flask.views import MethodView
from flask_smorest import abort

from app.auth import require_feature, require_role, require_role_types
from app.cohorts.progression import business_today
from app.lead_coverage import bp
from app.lead_coverage.models import LeadCoverage
from app.lead_coverage.schemas import (
    LeadCoverageCreateSchema, LeadCoverageEnvelopeSchema,
    LeadCoverageListEnvelopeSchema, LeadCoverageOptionsEnvelopeSchema,
)
from app.lead_coverage.services import _active_role, create_coverage, decide_coverage, eligible_leads, scope_coverages


@bp.before_request
def require_active_participant():
    if request.method == "OPTIONS":
        return None
    actor = getattr(g, "user", None)
    if not (_active_role(actor, "lead") or _active_role(actor, "manager")):
        abort(403, message="Lead coverage requires an active lead or manager.")


@bp.route("")
class LeadCoverages(MethodView):
    @require_feature("team", access="read")
    @require_feature("reports", access="read")
    @require_role_types("lead", "manager")
    @bp.response(200, LeadCoverageListEnvelopeSchema)
    def get(self):
        records = scope_coverages(g.user).order_by(LeadCoverage.created_at.desc(), LeadCoverage.id.desc()).all()
        return {"status": 200, "message": "Lead coverage retrieved.", "data": records}

    @require_feature("team", access="read")
    @require_feature("reports", access="read")
    @require_role_types("lead", "manager")
    @bp.arguments(LeadCoverageCreateSchema)
    @bp.response(201, LeadCoverageEnvelopeSchema)
    def post(self, data):
        record = create_coverage(g.user, data)
        message = "Coverage assigned." if record.status == "approved" else "Coverage requested. The covering lead must approve."
        return {"status": 201, "message": message, "data": record}


@bp.route("/options")
class LeadCoverageOptions(MethodView):
    @require_feature("team", access="read")
    @require_feature("reports", access="read")
    @require_role_types("lead", "manager")
    @bp.response(200, LeadCoverageOptionsEnvelopeSchema)
    def get(self):
        return {"status": 200, "message": "Eligible leads retrieved.", "data": {
            "leads": eligible_leads(g.user), "today": business_today(), "timezone": "Asia/Kolkata",
        }}


@bp.route("/<int:coverage_id>/approve")
class ApproveLeadCoverage(MethodView):
    @require_feature("team", access="read")
    @require_feature("reports", access="write")
    @require_role("lead")
    @bp.response(200, LeadCoverageEnvelopeSchema)
    def post(self, coverage_id):
        record = decide_coverage(coverage_id, g.user, "approve")
        return {"status": 200, "message": "Coverage approved.", "data": record}


@bp.route("/<int:coverage_id>/reject")
class RejectLeadCoverage(MethodView):
    @require_feature("team", access="read")
    @require_feature("reports", access="write")
    @require_role("lead")
    @bp.response(200, LeadCoverageEnvelopeSchema)
    def post(self, coverage_id):
        record = decide_coverage(coverage_id, g.user, "reject")
        return {"status": 200, "message": "Coverage rejected.", "data": record}


@bp.route("/<int:coverage_id>/cancel")
class CancelLeadCoverage(MethodView):
    @require_feature("team", access="read")
    @require_feature("reports", access="read")
    @require_role_types("lead", "manager")
    @bp.response(200, LeadCoverageEnvelopeSchema)
    def post(self, coverage_id):
        record = decide_coverage(coverage_id, g.user, "cancel")
        return {"status": 200, "message": "Coverage cancelled. Access has ended.", "data": record}
