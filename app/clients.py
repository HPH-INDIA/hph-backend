"""Explicit external-client surface; all project data reads are scoped by the API."""
from flask import g, request
from flask_smorest import abort


def project_user_ids(user):
    from app.roles.models import Role, RoleType
    from app.users.models import User
    return [member.id for member in User.query.join(Role).join(RoleType).filter(
        User.project_id == user.project_id, RoleType.code.in_(("lead", "employee"))) ] if user.project_id is not None else []


def enforce_client_access():
    user = getattr(g, "user", None)
    if user is None or user.role.role_type.code != "client" or not request.path.startswith("/api/") or request.method == "OPTIONS":
        return
    account_paths = {"/api/sessions/me", "/api/sessions/whoami", "/api/sessions/logout", "/api/users/set-password"}
    if request.path in account_paths or (request.path.startswith("/api/sessions/") and request.method == "DELETE"):
        return
    if request.method != "GET":
        abort(403, message="Client users have read-only access.")
    if user.project_id is None:
        abort(403, message="An assigned project is required for Client access.")
    from app.auth import has_feature
    reads = {
        "/api/dashboards/coding": "dashboard",
        "/api/dashboards/client": "dashboard",
        "/api/reports/kairon/completed-counts": "reports",
        "/api/reports/kairon/completed-users": "reports",
        "/api/reports/kairon/completed-records": "reports",
        "/api/reports/manual": "reports", "/api/reports/project-overview": "reports", "/api/kairon/charts": "reports",
        "/api/reports/kairon/holds": "chart_holds", "/api/reports/kairon/holds-summary": "chart_holds",
        "/api/login-hours/records": "login_hours", "/api/team/client-members": "team",
        "/api/users": "user_management", "/api/users/active": "user_management",
        "/api/users/inactive": "user_management", "/api/users/filter": "user_management",
        "/api/projects": "project_management", "/api/roles": "role_management",
        "/api/role-types": "role_management", "/api/features": "role_management",
    }
    if request.path == "/api/project-metric-theme":
        return
    required = reads.get(request.path)
    if required is None or not has_feature(user, required):
        abort(403, message="This menu is not allotted to this Client role.")
