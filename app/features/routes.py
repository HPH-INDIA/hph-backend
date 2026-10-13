from flask.views import MethodView
from flask_smorest import abort
from sqlalchemy.exc import IntegrityError

from app.auth import require_feature, require_role
from app.extensions import db
from app.features import bp
from app.features.models import Feature
from app.features.schemas import (
    AssignFeatureSchema,
    FeatureEnvelopeSchema,
    FeatureListEnvelopeSchema,
    FeatureSchema,
)
from app.roles.models import Role, role_features

feature_schema = FeatureSchema()
features_schema = FeatureSchema(many=True)


@bp.route("/features")
class Features(MethodView):
    @require_feature("role_management")
    @bp.response(200, FeatureListEnvelopeSchema)
    def get(self):
        query = Feature.query
        if Feature.query.filter_by(codename="team").first() is not None:
            query = query.filter(Feature.codename != "stage_view")
        features = query.all()
        return {"status": 200, "message": "Features retrieved successfully.", "data": features}

    @require_role("super_admin")
    @require_feature("role_management", access="write")
    @bp.arguments(FeatureSchema)
    @bp.response(201, FeatureEnvelopeSchema)
    def post(self, data):
        feature = Feature(**data)
        db.session.add(feature)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            abort(409, message="A feature with this codename already exists.")
        return {"status": 201, "message": "Feature created successfully.", "data": feature}


@bp.route("/features/<int:feature_id>")
class FeatureDetail(MethodView):
    @require_role("super_admin")
    @require_feature("role_management", access="write")
    @bp.arguments(FeatureSchema(partial=True))
    @bp.response(200, FeatureEnvelopeSchema)
    def patch(self, data, feature_id):
        feature = Feature.query.get_or_404(feature_id)
        for key, value in data.items():
            setattr(feature, key, value)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            abort(409, message="A feature with this codename already exists.")
        return {"status": 200, "message": "Feature updated successfully.", "data": feature}

    @require_role("super_admin")
    @require_feature("role_management", access="write")
    @bp.response(200, FeatureEnvelopeSchema)
    def delete(self, feature_id):
        # per §1a, "delete" here means deactivate — a hard delete would orphan
        # historical role_features rows and other records referencing this feature.
        feature = Feature.query.get_or_404(feature_id)
        feature.active = False
        db.session.commit()
        return {"status": 200, "message": "Feature deactivated successfully.", "data": feature}


@bp.route("/roles/<int:role_id>/features")
class RoleFeatures(MethodView):
    @require_feature("role_management")
    @bp.response(200, FeatureListEnvelopeSchema)
    def get(self, role_id):
        role = Role.query.get_or_404(role_id)
        return {
            "status": 200,
            "message": "Role features retrieved successfully.",
            "data": role.features,
        }

    @require_feature("role_management")
    @bp.arguments(AssignFeatureSchema)
    @bp.response(200, FeatureListEnvelopeSchema)
    def post(self, data, role_id):
        role = Role.query.get_or_404(role_id)
        if role.role_type.code == "client" and data.get("can_write", False):
            abort(422, message="Client roles are read-only.")
        feature = db.session.get(Feature, data["feature_id"])
        if feature is None:
            abort(400, message="feature_id must reference an existing feature.")

        if feature not in role.features:
            role.features.append(feature)
            db.session.flush()
        can_write = bool(data.get("can_write", False))
        db.session.execute(
            role_features.update()
            .where(role_features.c.role_id == role.id, role_features.c.feature_id == feature.id)
            .values(can_read=bool(data.get("can_read", True)) or can_write, can_write=can_write)
        )
        db.session.commit()
        return {
            "status": 200,
            "message": "Feature assigned to role successfully.",
            "data": role.features,
        }

    @require_feature("role_management")
    @bp.arguments(AssignFeatureSchema)
    @bp.response(200, FeatureListEnvelopeSchema)
    def delete(self, data, role_id):
        role = Role.query.get_or_404(role_id)
        feature = next((f for f in role.features if f.id == data["feature_id"]), None)
        if feature is None:
            abort(404, message="This role does not have that feature assigned.")

        role.features.remove(feature)
        db.session.commit()
        return {
            "status": 200,
            "message": "Feature revoked from role successfully.",
            "data": role.features,
        }
