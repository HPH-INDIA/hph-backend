from flask.views import MethodView

from app.roles.models import Role, RoleType
from app.users.models import Project, User
from appraisal_system.access_bff.directory import bp
from appraisal_system.access_bff.directory.schemas import (
    DirectoryProjectListEnvelopeSchema,
    DirectoryProjectQuerySchema,
    DirectoryUserListEnvelopeSchema,
    DirectoryUserQuerySchema,
)
from appraisal_system.access_bff.service_auth import require_service_caller


@bp.route("/users")
class DirectoryUsers(MethodView):
    @bp.arguments(DirectoryUserQuerySchema, location="query")
    @bp.response(200, DirectoryUserListEnvelopeSchema)
    def get(self, args):
        """Users matching every given filter, with role type and reporting line."""
        require_service_caller()

        query = User.query.join(Role, User.role_id == Role.id).join(
            RoleType, Role.role_type_id == RoleType.id
        )
        if args["ids"] is not None:
            query = query.filter(User.id.in_(args["ids"]))
        if args["project_id"] is not None:
            query = query.filter(User.project_id == args["project_id"])
        if args["role_types"]:
            query = query.filter(RoleType.code.in_(args["role_types"]))
        if args["active"] is not None:
            query = query.filter(User.is_active.is_(args["active"]))

        return {"status": 200, "message": "Users retrieved.", "data": query.order_by(User.id).all()}


@bp.route("/projects")
class DirectoryProjects(MethodView):
    @bp.arguments(DirectoryProjectQuerySchema, location="query")
    @bp.response(200, DirectoryProjectListEnvelopeSchema)
    def get(self, args):
        """Projects, optionally restricted to the given ids."""
        require_service_caller()

        query = Project.query
        if args["ids"] is not None:
            query = query.filter(Project.id.in_(args["ids"]))

        return {
            "status": 200,
            "message": "Projects retrieved.",
            "data": query.order_by(Project.id).all(),
        }
