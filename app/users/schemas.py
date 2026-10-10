from datetime import date

from marshmallow import Schema, ValidationError, fields, validate, validates, validates_schema

from app.extensions import db
from app.responses import envelope_schema
from app.roles.models import Role
from app.sessions.schemas import LoginEnvelopeSchema  # noqa: F401 (re-exported for routes.py)
from app.sessions.schemas import RoleProfileSchema
from app.users.models import User

PROJECT_REQUIRED_ROLE_TYPES = {"manager", "lead", "employee"}
PROJECT_FORBIDDEN_ROLE_TYPES = {"admin", "super_admin"}
REPORTING_PARENT_ROLE_TYPES = {"lead": "manager", "employee": "lead"}


def validate_project_for_role(role, project_id):
    """Raises ValueError if `project_id` doesn't match §2's rule for `role`'s
    role_type. Shared by UserCreateSchema (create) and the PATCH route
    (which must validate the *merged* result of a partial update).
    """
    role_type_code = role.role_type.code
    if role_type_code in PROJECT_REQUIRED_ROLE_TYPES and project_id is None:
        raise ValueError(f"project is required for the '{role.role_type.label}' role type.")
    if role_type_code in PROJECT_FORBIDDEN_ROLE_TYPES and project_id is not None:
        raise ValueError(f"project must not be set for the '{role.role_type.label}' role type.")


def validate_reporting_line(role, project_id, reports_to_id, user_id=None):
    """Validate the role-aware reporting line used to derive teams."""
    role_type_code = role.role_type.code
    required_parent_code = REPORTING_PARENT_ROLE_TYPES.get(role_type_code)
    if required_parent_code is None:
        if reports_to_id is not None:
            raise ValueError(f"reports_to_id must not be set for the '{role.role_type.label}' role type.")
        return
    if reports_to_id is None:
        raise ValueError(f"reports_to_id is required for the '{role.role_type.label}' role type.")
    if user_id is not None and reports_to_id == user_id:
        raise ValueError("A user cannot report to themselves.")

    parent = db.session.get(User, reports_to_id)
    if parent is None or not parent.is_active:
        raise ValueError("The reporting parent must be an active user.")
    if parent.role.role_type.code != required_parent_code:
        raise ValueError(f"The reporting parent must have the '{required_parent_code}' role type.")
    if parent.project_id != project_id:
        raise ValueError("The reporting parent must belong to the same project.")


class UserCreateSchema(Schema):
    email = fields.Email(required=True)
    first_name = fields.String(required=True, validate=validate.Length(min=1, max=128))
    last_name = fields.String(required=True, validate=validate.Length(min=1, max=128))
    join_date = fields.Date(allow_none=True, load_default=None)
    emp_id = fields.String(required=True, validate=validate.Length(min=1, max=64))
    role_id = fields.Integer(required=True)
    project_id = fields.Integer(allow_none=True, load_default=None)
    reports_to_id = fields.Integer(allow_none=True, load_default=None)

    @validates_schema
    def validate_project_matches_role_type(self, data, **kwargs):
        if "role_id" not in data:
            return  # partial (PATCH) load without a role change; route validates the merged result

        role = db.session.get(Role, data["role_id"])
        if role is None:
            raise ValidationError("Role not found.", field_name="role_id")

        try:
            validate_project_for_role(role, data.get("project_id"))
            validate_reporting_line(role, data.get("project_id"), data.get("reports_to_id"))
        except ValueError as exc:
            field_name = "reports_to_id" if "report" in str(exc).lower() else "project_id"
            raise ValidationError(str(exc), field_name=field_name) from exc


class UserSchema(Schema):
    id = fields.Integer(dump_only=True)
    email = fields.Email(dump_only=True)
    first_name = fields.String(dump_only=True)
    last_name = fields.String(dump_only=True)
    emp_id = fields.String(dump_only=True)
    role_id = fields.Integer(dump_only=True)
    role = fields.Method("get_role", dump_only=True)
    project_id = fields.Integer(dump_only=True, allow_none=True)
    reports_to_id = fields.Integer(dump_only=True, allow_none=True)
    first_login = fields.Boolean(dump_only=True)
    is_active = fields.Boolean(dump_only=True)
    join_date = fields.Date(dump_only=True, allow_none=True)
    last_working_day = fields.Date(dump_only=True, allow_none=True)
    created_at = fields.DateTime(dump_only=True)
    updated_at = fields.DateTime(dump_only=True)

    def get_role(self, user):
        # Same nested shape UserProfileSchema uses at login, so the frontend
        # never needs to call the features API to know what a role can do.
        return RoleProfileSchema().dump(user.role)


class DeactivateUserSchema(Schema):
    last_working_day = fields.Date(required=True)

    @validates("last_working_day")
    def validate_last_working_day(self, value, **kwargs):
        if value > date.today():
            raise ValidationError("Last working day cannot be in the future.")


class UserFilterQuerySchema(Schema):
    # Repeatable query params (?projectIds=1&projectIds=2), AND'd together
    # when both are given - a user must match at least one of each.
    project_ids = fields.List(fields.Integer(), required=False, load_default=None, data_key="projectIds")
    role_ids = fields.List(fields.Integer(), required=False, load_default=None, data_key="roleIds")


class ManagerTeamQuerySchema(Schema):
    page = fields.Integer(required=False, load_default=1, validate=validate.Range(min=1))
    page_size = fields.Integer(
        required=False, load_default=25, validate=validate.Range(min=1, max=100), data_key="pageSize"
    )
    search = fields.String(required=False, load_default=None, validate=validate.Length(max=128))
    # 0 means unassigned; omitted means all coders in the manager's team.
    lead_id = fields.Integer(required=False, load_default=None, validate=validate.Range(min=0), data_key="leadId")


class TeamAssignmentSchema(Schema):
    coder_ids = fields.List(
        fields.Integer(), required=True, validate=validate.Length(min=1, max=100), data_key="coderIds"
    )
    lead_id = fields.Integer(required=True, data_key="leadId")


class TeamLeadSummarySchema(Schema):
    id = fields.Integer(dump_only=True)
    first_name = fields.String(dump_only=True, data_key="firstName")
    last_name = fields.String(dump_only=True, data_key="lastName")
    emp_id = fields.String(dump_only=True, data_key="empId")
    coder_count = fields.Integer(dump_only=True, data_key="coderCount")


class UserPageSchema(Schema):
    items = fields.List(fields.Nested(UserSchema), dump_only=True)
    page = fields.Integer(dump_only=True)
    page_size = fields.Integer(dump_only=True, data_key="pageSize")
    total = fields.Integer(dump_only=True)
    total_pages = fields.Integer(dump_only=True, data_key="totalPages")


class ManagerTeamSchema(Schema):
    leads = fields.List(fields.Nested(TeamLeadSummarySchema), dump_only=True)
    coders = fields.Nested(UserPageSchema, dump_only=True)
    total_coders = fields.Integer(dump_only=True, data_key="totalCoders")
    unassigned_count = fields.Integer(dump_only=True, data_key="unassignedCount")


class SetPasswordSchema(Schema):
    current_password = fields.String(required=True)
    new_password = fields.String(required=True, validate=validate.Length(min=8))
    new_password_confirm = fields.String(required=True)

    @validates_schema
    def validate_passwords_match(self, data, **kwargs):
        if data.get("new_password") != data.get("new_password_confirm"):
            raise ValidationError("Passwords do not match.", field_name="new_password_confirm")


UserEnvelopeSchema = envelope_schema("UserEnvelopeSchema", fields.Nested(UserSchema))
UserListEnvelopeSchema = envelope_schema("UserListEnvelopeSchema", fields.List(fields.Nested(UserSchema)))
ManagerTeamEnvelopeSchema = envelope_schema("ManagerTeamEnvelopeSchema", fields.Nested(ManagerTeamSchema))
