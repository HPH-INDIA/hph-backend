from flask import g
from flask.views import MethodView
from flask_smorest import Blueprint, abort
from marshmallow import Schema, fields, validate
from sqlalchemy.exc import IntegrityError

from app.auth import require_feature, require_role_types
from app.entra.services import identity_dict, link_identity, unlink_identity
from app.extensions import db
from app.responses import envelope_schema
from app.users.hierarchy import can_manage_role_type
from app.users.models import User

bp = Blueprint("entra_admin", __name__, url_prefix="/api", description="Administrator-managed Entra identity links")


class IdentityInput(Schema):
    object_id = fields.UUID(required=True)
    account_type = fields.String(required=True, validate=validate.OneOf(["member", "guest"]))


IdentityEnvelope = envelope_schema("IdentityEnvelope", fields.Raw(allow_none=True))


def target_user(user_id):
    user = db.session.get(User, user_id)
    if not user:
        abort(404, message="User not found.")
    if not can_manage_role_type(g.user.role.role_type.code, user.role.role_type.code):
        abort(403, message="You cannot manage identity links for this role.")
    return user


@bp.route("/users/<int:user_id>/entra-identity")
class IdentityLink(MethodView):
    decorators = [require_role_types("admin", "super_admin"), require_feature("user_management", "write")]

    @bp.response(200, IdentityEnvelope)
    def get(self, user_id):
        return {"status": 200, "message": "Entra identity retrieved.", "data": identity_dict(target_user(user_id).entra_identity)}

    @bp.arguments(IdentityInput)
    @bp.response(200, IdentityEnvelope)
    def put(self, data, user_id):
        user = target_user(user_id)
        try:
            identity = link_identity(user, data["object_id"], data["account_type"], actor=g.user)
            db.session.commit()
        except ValueError as exc:
            db.session.rollback()
            abort(400, message=str(exc))
        except IntegrityError:
            db.session.rollback()
            abort(409, message="This identity is already linked. Refresh and try again.")
        return {"status": 200, "message": "Entra identity linked. Existing sessions were revoked.", "data": identity_dict(identity)}

    @bp.response(200, IdentityEnvelope)
    def delete(self, user_id):
        unlink_identity(target_user(user_id), actor=g.user)
        db.session.commit()
        return {"status": 200, "message": "Microsoft access removed and sessions revoked. Password access is unchanged.", "data": None}
