from flask.views import MethodView
from flask_smorest import abort
from sqlalchemy.exc import IntegrityError

from app.auth import require_feature, require_role
from app.extensions import db
from app.features.models import Feature
from app.responses import MessageEnvelopeSchema
from app.roles import bp
from app.roles.models import Role, RoleType, role_features
from app.roles.schemas import (
    RoleEnvelopeSchema,
    RoleListEnvelopeSchema,
    RoleSchema,
    RoleTypeListEnvelopeSchema,
    SessionTimeoutEnvelopeSchema,
    SessionTimeoutUpdateSchema,
)


def _set_role_features(role, feature_ids, feature_permissions=None):
    """Replace a role's feature assignments and persist their access levels.

    Legacy callers that only send feature ids retain the historical full-access
    behavior. New callers send feature_permissions for explicit read/write grants.
    """
    assignments = feature_permissions
    if assignments is None:
        assignments = [
            {"feature_id": feature_id, "can_read": True, "can_write": True}
            for feature_id in feature_ids
        ]

    ids = [assignment["feature_id"] for assignment in assignments]
    role.features = Feature.query.filter(Feature.id.in_(ids)).all() if ids else []
    db.session.flush()

    for assignment in assignments:
        can_write = bool(assignment.get("can_write", False))
        db.session.execute(
            role_features.update()
            .where(
                role_features.c.role_id == role.id,
                role_features.c.feature_id == assignment["feature_id"],
            )
            .values(
                can_read=bool(assignment.get("can_read", True)) or can_write,
                can_write=can_write,
            )
        )


def _validate_role_scope(data, role=None):
    role_type = db.session.get(RoleType, data.get("role_type_id", role.role_type_id if role else None))
    if role_type.code != "client":
        if role:
            from app.entra.models import ExternalIdentity
            from app.users.models import User
            if db.session.query(ExternalIdentity.id).join(User, User.id == ExternalIdentity.user_id).filter(
                    User.role_id == role.id, ExternalIdentity.account_type == "guest").first():
                abort(422, message="A role assigned to invited guests must remain Client.")
        return
    permissions = data.get("feature_permissions")
    if permissions is not None and any(p["can_write"] for p in permissions):
        abort(422, message="Client roles are read-only.")
    if permissions is None:
        if data.get("features"):
            abort(422, message="Client features require explicit read-only permissions.")
        if role and "features" not in data and db.session.execute(db.select(role_features.c.role_id).where(role_features.c.role_id == role.id, role_features.c.can_write.is_(True))).first():
            abort(422, message="Remove write permissions before changing to Client.")


@bp.route("/role-types")
class RoleTypes(MethodView):
    # read-only, fixed seed data (§1) — ungated beyond the normal
    # session-login requirement, the same way GET /api/cohorts is: any
    # authenticated user may view it, only role_management can write it.
    @bp.response(200, RoleTypeListEnvelopeSchema)
    def get(self):
        role_types = RoleType.query.order_by(RoleType.hierarchy_rank).all()
        return {"status": 200, "message": "Role types retrieved successfully.", "data": role_types}


@bp.route("/role-types/<int:role_type_id>/session-timeout")
class RoleTypeSessionTimeout(MethodView):
    @require_role("super_admin")
    @bp.response(200, SessionTimeoutEnvelopeSchema)
    def get(self, role_type_id):
        role_type = RoleType.query.get_or_404(role_type_id)
        return {
            "status": 200,
            "message": "Session timeout retrieved successfully.",
            "data": role_type,
        }

    @require_role("super_admin")
    @bp.arguments(SessionTimeoutUpdateSchema)
    @bp.response(200, SessionTimeoutEnvelopeSchema)
    def patch(self, payload, role_type_id):
        role_type = RoleType.query.get_or_404(role_type_id)
        role_type.session_timeout_minutes = payload["session_timeout_minutes"]
        db.session.commit()
        return {
            "status": 200,
            "message": "Session timeout updated successfully.",
            "data": role_type,
        }


@bp.route("/roles")
class Roles(MethodView):
    # Read is ungated beyond login (same rationale as RoleTypes.get above);
    # only creating/editing a role stays behind role_management.
    @bp.response(200, RoleListEnvelopeSchema)
    def get(self):
        roles = Role.query.all()
        return {"status": 200, "message": "Roles retrieved successfully.", "data": roles}

    @require_feature("role_management")
    @bp.arguments(RoleSchema)
    @bp.response(201, RoleEnvelopeSchema)
    def post(self, data):
        _validate_role_scope(data)
        feature_ids = data.pop("features", [])
        feature_permissions = data.pop("feature_permissions", None)
        role = Role(**data)
        db.session.add(role)
        _set_role_features(role, feature_ids, feature_permissions)
        db.session.commit()
        return {"status": 201, "message": "Role created successfully.", "data": role}


@bp.route("/roles/<int:role_id>")
class RoleDetail(MethodView):
    # Read is ungated beyond login (same rationale as Roles.get above);
    # write methods below stay behind role_management.
    @bp.response(200, RoleEnvelopeSchema)
    def get(self, role_id):
        role = Role.query.get_or_404(role_id)
        return {"status": 200, "message": "Role retrieved successfully.", "data": role}

    @require_feature("role_management")
    @bp.arguments(RoleSchema(partial=True))
    @bp.response(200, RoleEnvelopeSchema)
    def patch(self, data, role_id):
        role = Role.query.get_or_404(role_id)
        _validate_role_scope(data, role)
        feature_ids = data.pop("features", None)
        feature_permissions = data.pop("feature_permissions", None)
        for key, value in data.items():
            setattr(role, key, value)
        if feature_ids is not None or feature_permissions is not None:
            _set_role_features(role, feature_ids or [], feature_permissions)

        db.session.commit()
        return {"status": 200, "message": "Role updated successfully.", "data": role}

    @require_feature("role_management")
    @bp.response(200, MessageEnvelopeSchema)
    def delete(self, role_id):
        role = Role.query.get_or_404(role_id)
        db.session.delete(role)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            abort(409, message="This role is still assigned to one or more users and cannot be deleted.")
        return {"status": 200, "message": "Role deleted successfully.", "data": None}
