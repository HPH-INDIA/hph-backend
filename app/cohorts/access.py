"""Stage readers have a narrower feature and scope than team administrators."""
from functools import wraps

from flask import g
from flask_smorest import abort

from app.auth import has_feature
from app.users.hierarchy import manager_team_user_ids, report_team_user_ids


def require_stage_read_access(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = getattr(g, "user", None)
        role = user.role.role_type.code if user else None
        if role == "client":
            permitted = False
        elif role == "manager":
            permitted = has_feature(user, "team")
        else:
            permitted = (role in ("lead", "employee") and has_feature(user, "team")
                         and user.project is not None and user.project.name == "CODING")
        if not permitted:
            abort(403, message="You do not have read access to stages.")
        return view(*args, **kwargs)
    return wrapped


def stage_reader_user_ids(user):
    role = user.role.role_type.code
    if role == "client":
        return []
    if role == "manager":
        return manager_team_user_ids(user.id)
    if role == "lead":
        return report_team_user_ids(user)
    if role == "employee":
        return [user.id]
    return []
