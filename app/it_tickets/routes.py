from flask import g
from flask.views import MethodView

from app.auth import require_feature, require_role_types
from app.extensions import db
from app.it_tickets import bp, services
from app.it_tickets.models import IssueCategory
from app.it_tickets.schemas import (
    CategoryCreateSchema, CategoryQuerySchema, CategoryUpdateSchema, DataEnvelopeSchema, TicketCreateSchema,
    TicketDeleteSchema, TicketImportSchema, TicketQuerySchema, TicketUpdateSchema,
)
from flask_smorest import abort

MANAGERS = ("manager", "admin", "super_admin")
ADMINS = ("admin", "super_admin")
TICKET_ROLES = ("employee", "lead", "manager", "admin", "super_admin")


def ok(data, message="OK", status=200):
    return {"status": status, "message": message, "data": data}


@bp.route("/it-tickets")
class Tickets(MethodView):
    @require_feature("it_tickets", access="read")
    @require_role_types(*TICKET_ROLES)
    @bp.arguments(TicketQuerySchema, location="query")
    @bp.response(200, DataEnvelopeSchema)
    def get(self, args):
        return ok(services.list_tickets(g.user, args), "Tickets retrieved.")

    @require_feature("it_tickets", access="write")
    @require_role_types(*TICKET_ROLES)
    @bp.arguments(TicketCreateSchema)
    @bp.response(201, DataEnvelopeSchema)
    def post(self, data):
        ticket = services.create_ticket(g.user, data)
        return ok(services.serialize_ticket(ticket, g.user), f"{ticket.jira_key} logged.", 201)


for _path, _fn, _message in (
    ("/it-tickets/summary", services.summary, "Summary retrieved."),
    ("/it-tickets/practice-wise", services.practice_wise, "Practice-wise view retrieved."),
    ("/it-tickets/coder-wise", services.coder_wise, "Coder-wise view retrieved."),
    ("/it-tickets/access-matrix", services.access_matrix, "Access view retrieved."),
):
    def _make(fn, message):
        class _View(MethodView):
            @require_feature("it_tickets", access="read")
            @require_role_types(*MANAGERS)
            @bp.arguments(TicketQuerySchema, location="query")
            @bp.response(200, DataEnvelopeSchema)
            def get(self, args):
                return ok(fn(g.user, args), message)

        _View.__name__ = f"TicketView_{fn.__name__}"
        return _View

    bp.route(_path)(_make(_fn, _message))


@bp.route("/it-tickets/filter-options")
class TicketFilterOptions(MethodView):
    @require_feature("it_tickets", access="read")
    @require_role_types(*TICKET_ROLES)
    @bp.response(200, DataEnvelopeSchema)
    def get(self):
        return ok(services.filter_options(g.user), "Filter options retrieved.")


@bp.route("/it-tickets/<int:ticket_id>")
class TicketDetail(MethodView):
    @require_feature("it_tickets", access="read")
    @require_role_types(*TICKET_ROLES)
    @bp.response(200, DataEnvelopeSchema)
    def get(self, ticket_id):
        return ok(services.ticket_detail(g.user, services.get_ticket(g.user, ticket_id)), "Ticket retrieved.")

    @require_feature("it_tickets", access="write")
    @require_role_types(*TICKET_ROLES)
    @bp.arguments(TicketUpdateSchema)
    @bp.response(200, DataEnvelopeSchema)
    def patch(self, data, ticket_id):
        ticket = services.update_ticket(g.user, services.get_ticket(g.user, ticket_id), data)
        return ok(services.serialize_ticket(ticket, g.user), "Ticket updated.")

    @require_feature("it_tickets", access="write")
    @require_role_types(*MANAGERS)
    @bp.arguments(TicketDeleteSchema)
    @bp.response(200, DataEnvelopeSchema)
    def delete(self, data, ticket_id):
        ticket = services.get_ticket(g.user, ticket_id)
        key = ticket.jira_key
        services.delete_ticket(g.user, ticket, data["reason"], data.get("note"))
        return ok(None, f"{key} deleted.")


@bp.route("/issue-categories")
class IssueCategories(MethodView):
    @require_feature("it_tickets", access="read")
    @bp.arguments(CategoryQuerySchema, location="query")
    @bp.response(200, DataEnvelopeSchema)
    def get(self, args):
        return ok(services.list_categories(args["include_retired"]), "Issue categories retrieved.")

    @require_feature("it_tickets", access="write")
    @require_role_types(*ADMINS)
    @bp.arguments(CategoryCreateSchema)
    @bp.response(201, DataEnvelopeSchema)
    def post(self, data):
        return ok(services.save_category(IssueCategory(active=True), data), "Issue category added.", 201)


@bp.route("/issue-categories/<int:category_id>")
class IssueCategoryDetail(MethodView):
    @require_feature("it_tickets", access="write")
    @require_role_types(*ADMINS)
    @bp.arguments(CategoryUpdateSchema)
    @bp.response(200, DataEnvelopeSchema)
    def patch(self, data, category_id):
        category = db.session.get(IssueCategory, category_id)
        if category is None:
            abort(404, message="Issue category not found.")
        return ok(services.save_category(category, data), "Issue category updated.")


@bp.route("/it-tickets/imports/preview")
class TicketImportPreview(MethodView):
    @require_feature("it_tickets", access="write")
    @require_role_types(*MANAGERS)
    @bp.arguments(TicketImportSchema)
    @bp.response(200, DataEnvelopeSchema)
    def post(self, data):
        from app.it_tickets.importer import preview_ticket_import
        return ok(preview_ticket_import(g.user, data["rows"], data["user_mappings"], data["practice_mappings"]), "Preview ready.")


@bp.route("/it-tickets/imports/commit")
class TicketImportCommit(MethodView):
    @require_feature("it_tickets", access="write")
    @require_role_types(*MANAGERS)
    @bp.arguments(TicketImportSchema)
    @bp.response(200, DataEnvelopeSchema)
    def post(self, data):
        from app.it_tickets.importer import commit_ticket_import
        return ok(commit_ticket_import(g.user, data["rows"], data["user_mappings"], data["practice_mappings"]), "Tickets imported.")
