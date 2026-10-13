from marshmallow import Schema, ValidationError, fields, post_dump, validate, validates_schema

from app.extensions import db
from app.features.models import Feature
from app.responses import envelope_schema
from app.roles.models import RoleType


class RoleFeaturePermissionSchema(Schema):
    feature_id = fields.Integer(required=True)
    can_read = fields.Boolean(load_default=True)
    can_write = fields.Boolean(load_default=False)


class RoleSchema(Schema):
    id = fields.Integer(dump_only=True)
    title = fields.String(required=True, validate=validate.Length(min=1, max=128))
    role_type_id = fields.Integer(required=True)
    is_active = fields.Boolean(load_default=True)
    features = fields.List(fields.Integer(), load_only=True, load_default=list)
    feature_permissions = fields.List(
        fields.Nested(RoleFeaturePermissionSchema),
        load_default=None,
    )
    created_at = fields.DateTime(dump_only=True)
    updated_at = fields.DateTime(dump_only=True)

    @validates_schema
    def validate_role_type_and_features(self, data, **kwargs):
        if "role_type_id" in data and db.session.get(RoleType, data["role_type_id"]) is None:
            raise ValidationError("RoleType not found.", field_name="role_type_id")

        permissions = data.get("feature_permissions")
        if permissions is not None:
            feature_ids = [permission["feature_id"] for permission in permissions]
            if len(feature_ids) != len(set(feature_ids)):
                raise ValidationError("A feature may only be assigned once.", field_name="feature_permissions")
            for permission in permissions:
                if permission["can_write"]:
                    permission["can_read"] = True
                if not permission["can_read"] and not permission["can_write"]:
                    raise ValidationError(
                        "An enabled feature must grant read or write access.",
                        field_name="feature_permissions",
                    )

            submitted_feature_ids = data.get("features")
            if submitted_feature_ids and set(submitted_feature_ids) != set(feature_ids):
                raise ValidationError(
                    "features and feature_permissions must reference the same features.",
                    field_name="feature_permissions",
                )
        else:
            feature_ids = data.get("features")

        if feature_ids:
            found = Feature.query.filter(Feature.id.in_(feature_ids)).count()
            if found != len(set(feature_ids)):
                raise ValidationError("One or more feature ids are invalid.", field_name="features")

    @post_dump(pass_original=True)
    def add_feature_ids(self, data, original, **kwargs):
        from app.roles.permissions import canonical_permissions
        permissions = canonical_permissions(original)
        data["features"] = [permission["feature_id"] for permission in permissions]
        data["feature_permissions"] = permissions
        return data


class SessionTimeoutSchema(Schema):
    id = fields.Integer(data_key="roleTypeId")
    session_timeout_minutes = fields.Integer(data_key="sessionTimeoutMinutes")


class SessionTimeoutUpdateSchema(Schema):
    session_timeout_minutes = fields.Integer(required=True, validate=validate.Range(min=1))


class RoleTypeSchema(Schema):
    id = fields.Integer(dump_only=True)
    code = fields.String(dump_only=True)
    label = fields.String(dump_only=True)
    hierarchy_rank = fields.Integer(dump_only=True, data_key="hierarchyRank")
    session_timeout_minutes = fields.Integer(dump_only=True, data_key="sessionTimeoutMinutes")


RoleEnvelopeSchema = envelope_schema("RoleEnvelopeSchema", fields.Nested(RoleSchema))
RoleListEnvelopeSchema = envelope_schema("RoleListEnvelopeSchema", fields.List(fields.Nested(RoleSchema)))
SessionTimeoutEnvelopeSchema = envelope_schema(
    "SessionTimeoutEnvelopeSchema", fields.Nested(SessionTimeoutSchema)
)
RoleTypeListEnvelopeSchema = envelope_schema(
    "RoleTypeListEnvelopeSchema", fields.List(fields.Nested(RoleTypeSchema))
)
