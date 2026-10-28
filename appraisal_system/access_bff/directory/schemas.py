from marshmallow import Schema, ValidationError, fields

from app.responses import envelope_schema


class CommaSeparatedIntegers(fields.Field):
    """``"1,2,3"`` in a query string, loaded as a list of ints."""

    def _deserialize(self, value, attr, data, **kwargs):
        try:
            return [int(item) for item in str(value).split(",") if item.strip()]
        except ValueError as exc:
            raise ValidationError("Must be comma-separated integers.") from exc


class CommaSeparatedStrings(fields.Field):
    def _deserialize(self, value, attr, data, **kwargs):
        return [item.strip() for item in str(value).split(",") if item.strip()]


class DirectoryUserQuerySchema(Schema):
    ids = CommaSeparatedIntegers(load_default=None)
    project_id = fields.Integer(load_default=None, data_key="projectId")
    role_types = CommaSeparatedStrings(load_default=None, data_key="roleTypes")
    active = fields.Boolean(load_default=None)


class DirectoryProjectQuerySchema(Schema):
    ids = CommaSeparatedIntegers(load_default=None)


class DirectoryUserSchema(Schema):
    id = fields.Integer()
    emp_id = fields.String(data_key="empId")
    first_name = fields.String(data_key="firstName")
    last_name = fields.String(data_key="lastName")
    email = fields.String()
    role_type_code = fields.Function(lambda user: user.role.role_type.code, data_key="roleTypeCode")
    role_title = fields.Function(lambda user: user.role.title, data_key="roleTitle")
    project_id = fields.Integer(allow_none=True, data_key="projectId")
    reports_to_id = fields.Integer(allow_none=True, data_key="reportsToId")
    is_active = fields.Boolean(data_key="isActive")
    last_working_day = fields.Date(allow_none=True, data_key="lastWorkingDay")


class DirectoryProjectSchema(Schema):
    id = fields.Integer()
    name = fields.String()


DirectoryUserListEnvelopeSchema = envelope_schema(
    "DirectoryUserListEnvelopeSchema", fields.List(fields.Nested(DirectoryUserSchema))
)
DirectoryProjectListEnvelopeSchema = envelope_schema(
    "DirectoryProjectListEnvelopeSchema", fields.List(fields.Nested(DirectoryProjectSchema))
)
