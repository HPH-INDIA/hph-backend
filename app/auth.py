from functools import wraps

from flask import g, request
from flask_smorest import abort

from app.extensions import db


def _forbidden(message):
    abort(403, message=message)


def has_role(user, role_type_code):
    return user is not None and user.role.role_type.code == role_type_code


def has_feature(user, codename, access="read"):
    # Import lazily: app.roles registers routes that import these decorators,
    # so importing its models while this module initializes creates a cycle.
    from app.roles.models import role_features

    if user is None:
        return False
    if user.role.role_type.code == "client" and access == "write":
        return False

    canonical = "team" if codename == "stage_view" else codename
    if canonical == "team":
        from app.features.models import Feature
        team = Feature.query.filter_by(codename="team").first()
        if team is not None and not team.active:
            return False
    names = {canonical, "stage_view"} if canonical == "team" and access == "read" else {canonical}
    ids = [feature.id for feature in user.role.features if feature.codename in names and feature.active]
    if not ids:
        return False
    permissions = db.session.execute(db.select(role_features.c.can_read, role_features.c.can_write).where(
        role_features.c.role_id == user.role_id, role_features.c.feature_id.in_(ids))).all()
    return any(bool(permission.can_write) if access == "write" else bool(permission.can_read or permission.can_write)
               for permission in permissions)


def require_role(role_type_code):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not has_role(getattr(g, "user", None), role_type_code):
                return _forbidden(f"This action requires the '{role_type_code}' role type.")
            return view_func(*args, **kwargs)

        return wrapped

    return decorator


def require_role_types(*role_type_codes):
    allowed = set(role_type_codes)

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            user = getattr(g, "user", None)
            if user is None or user.role.role_type.code not in allowed:
                return _forbidden(
                    f"This action requires one of these role types: {', '.join(sorted(allowed))}."
                )
            return view_func(*args, **kwargs)

        return wrapped

    return decorator


def require_feature(codename, access=None):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            required_access = access or ("read" if request.method in {"GET", "HEAD", "OPTIONS"} else "write")
            if not has_feature(getattr(g, "user", None), codename, required_access):
                return _forbidden(
                    f"This action requires {required_access} access to the '{codename}' feature."
                )
            return view_func(*args, **kwargs)

        return wrapped

    return decorator
