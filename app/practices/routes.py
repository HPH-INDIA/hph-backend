from flask import g
from flask.views import MethodView
from flask_smorest import abort

from app.auth import has_feature, require_feature, require_role_types
from app.practices import bp, services
from app.practices.schemas import (
    AssignmentCreateSchema, AssignmentUpdateSchema, DataEnvelopeSchema, PracticeCreateSchema,
    PracticeImportSchema, PracticeListQuerySchema, PracticeRequestCreateSchema, PracticeRequestQuerySchema,
    PracticeRequestResolveSchema, PracticeStatusSchema, PracticeWriteSchema,
)

MANAGERS = ("manager", "admin", "super_admin")


def ok(data, message="OK", status=200):
    return {"status": status, "message": message, "data": data}


@bp.route("/practices")
class Practices(MethodView):
    @require_feature("practices", access="read")
    @require_role_types(*MANAGERS)
    @bp.arguments(PracticeListQuerySchema, location="query")
    @bp.response(200, DataEnvelopeSchema)
    def get(self, args):
        return ok(services.list_practices(g.user, args), "Practices retrieved.")

    @require_feature("practices", access="write")
    @require_role_types(*MANAGERS)
    @bp.arguments(PracticeCreateSchema)
    @bp.response(201, DataEnvelopeSchema)
    def post(self, data):
        practice = services.create_practice(g.user, data)
        return ok(services.serialize_practice(practice), "Practice added.", 201)


@bp.route("/practices/<int:practice_id>")
class PracticeDetail(MethodView):
    @require_feature("practices", access="read")
    @require_role_types(*MANAGERS)
    @bp.response(200, DataEnvelopeSchema)
    def get(self, practice_id):
        return ok(services.serialize_practice(services.get_practice(g.user, practice_id)), "Practice retrieved.")

    @require_feature("practices", access="write")
    @require_role_types(*MANAGERS)
    @bp.arguments(PracticeWriteSchema)
    @bp.response(200, DataEnvelopeSchema)
    def patch(self, data, practice_id):
        practice = services.update_practice(g.user, services.get_practice(g.user, practice_id), data)
        return ok(services.serialize_practice(practice), "Practice updated.")

    @require_feature("practices", access="write")
    @require_role_types(*MANAGERS)
    @bp.response(200, DataEnvelopeSchema)
    def delete(self, practice_id):
        services.delete_practice(services.get_practice(g.user, practice_id))
        return ok(None, "Practice deleted.")


@bp.route("/practices/<int:practice_id>/status")
class PracticeStatus(MethodView):
    @require_feature("practices", access="write")
    @require_role_types(*MANAGERS)
    @bp.arguments(PracticeStatusSchema)
    @bp.response(200, DataEnvelopeSchema)
    def post(self, data, practice_id):
        practice = services.get_practice(g.user, practice_id)
        resolved = services.change_status(g.user, practice, data["status"], data.get("effective_from"), data.get("reason"))
        message = f"Practice set to {practice.status}."
        if resolved:
            message += f" {resolved} open ticket(s) resolved automatically."
        return ok({"practice": services.serialize_practice(practice), "autoResolvedCount": resolved}, message)


@bp.route("/practices/<int:practice_id>/history")
class PracticeHistory(MethodView):
    @require_feature("practices", access="read")
    @require_role_types(*MANAGERS)
    @bp.response(200, DataEnvelopeSchema)
    def get(self, practice_id):
        return ok(services.status_history(services.get_practice(g.user, practice_id)), "History retrieved.")


@bp.route("/practices/<int:practice_id>/assignments")
class PracticeAssignments(MethodView):
    @require_feature("practices", access="write")
    @require_role_types(*MANAGERS)
    @bp.arguments(AssignmentCreateSchema)
    @bp.response(201, DataEnvelopeSchema)
    def post(self, data, practice_id):
        assignment = services.add_assignment(g.user, services.get_practice(g.user, practice_id), data)
        return ok(services.serialize_assignment(assignment), "Person assigned.", 201)


@bp.route("/practice-assignments/<int:assignment_id>")
class PracticeAssignmentDetail(MethodView):
    @require_feature("practices", access="write")
    @require_role_types(*MANAGERS)
    @bp.arguments(AssignmentUpdateSchema)
    @bp.response(200, DataEnvelopeSchema)
    def patch(self, data, assignment_id):
        assignment = services.update_assignment(services.get_assignment(g.user, assignment_id), data)
        return ok(services.serialize_assignment(assignment), "Assignment updated.")

    @require_feature("practices", access="write")
    @require_role_types(*MANAGERS)
    @bp.response(200, DataEnvelopeSchema)
    def delete(self, assignment_id):
        services.delete_assignment(services.get_assignment(g.user, assignment_id))
        return ok(None, "Assignment removed.")


@bp.route("/practices/mine")
class MyPractices(MethodView):
    @bp.response(200, DataEnvelopeSchema)
    def get(self):
        if not (has_feature(g.user, "it_tickets") or has_feature(g.user, "practices")):
            abort(403, message="This action requires read access to the 'it_tickets' feature.")
        return ok(services.my_practices(g.user), "Your practices retrieved.")


@bp.route("/practices/assignable-users")
class AssignableUsers(MethodView):
    @require_feature("practices", access="read")
    @require_role_types(*MANAGERS)
    @bp.response(200, DataEnvelopeSchema)
    def get(self):
        return ok(services.assignable_users(g.user), "People retrieved.")


@bp.route("/practice-requests")
class PracticeRequests(MethodView):
    @require_feature("practices", access="read")
    @require_role_types(*MANAGERS)
    @bp.arguments(PracticeRequestQuerySchema, location="query")
    @bp.response(200, DataEnvelopeSchema)
    def get(self, args):
        return ok(services.list_requests(g.user, args["status"]), "Requests retrieved.")

    @require_feature("it_tickets", access="read")
    @bp.arguments(PracticeRequestCreateSchema)
    @bp.response(201, DataEnvelopeSchema)
    def post(self, data):
        row = services.create_request(g.user, data)
        return ok(services.serialize_request(row), "Request sent to your manager.", 201)


@bp.route("/practice-requests/<int:request_id>/resolve")
class ResolvePracticeRequest(MethodView):
    @require_feature("practices", access="write")
    @require_role_types(*MANAGERS)
    @bp.arguments(PracticeRequestResolveSchema)
    @bp.response(200, DataEnvelopeSchema)
    def post(self, data, request_id):
        row = services.resolve_request(g.user, request_id, data)
        return ok(services.serialize_request(row), "Request resolved.")


@bp.route("/practices/imports/preview")
class PracticeImportPreview(MethodView):
    @require_feature("practices", access="write")
    @require_role_types(*MANAGERS)
    @bp.arguments(PracticeImportSchema)
    @bp.response(200, DataEnvelopeSchema)
    def post(self, data):
        from app.practices.importer import preview_practice_import
        return ok(preview_practice_import(g.user, data["rows"], data["user_mappings"]), "Preview ready.")


@bp.route("/practices/imports/commit")
class PracticeImportCommit(MethodView):
    @require_feature("practices", access="write")
    @require_role_types(*MANAGERS)
    @bp.arguments(PracticeImportSchema)
    @bp.response(200, DataEnvelopeSchema)
    def post(self, data):
        from app.practices.importer import commit_practice_import
        return ok(commit_practice_import(g.user, data["rows"], data["user_mappings"]), "Practices imported.")
