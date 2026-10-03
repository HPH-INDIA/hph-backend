"""Handlers for /forms; operationIds in form_builder.yaml point here."""

from common.connexion_app import current_caller
from services import forms as forms_service, serializers
from services.permissions import require_appraisal_service_or_form_reader, require_form_builder


def list_forms(status=None, project_id=None, role_type=None):
    require_form_builder(current_caller(), "read")

    forms = forms_service.list_forms(status=status, project_id=project_id, role_type=role_type)

    return [serializers.form_summary_to_dict(form) for form in forms], 200


def create_form(body):
    caller = current_caller()
    require_form_builder(caller, "write")

    form = forms_service.create_form(
        caller,
        name=body.get("name"),
        description=body.get("description"),
        purpose=body.get("purpose", "appraisal"),
        from_template_id=body.get("from_template_id"),
        definition=body.get("definition"),
    )

    return serializers.form_detail_to_dict(form), 201


def get_form(form_id):
    require_form_builder(current_caller(), "read")

    return serializers.form_detail_to_dict(forms_service.get_form(form_id)), 200


def update_form(form_id, body):
    caller = current_caller()
    require_form_builder(caller, "write")

    form = forms_service.update_form(caller, form_id, body)

    return serializers.form_detail_to_dict(form), 200


def delete_form(form_id):
    caller = current_caller()
    require_form_builder(caller, "write")

    forms_service.delete_form(caller, form_id)

    return {"message": f"form {form_id} deleted", "success": True}, 200


def publish_form(form_id):
    caller = current_caller()
    require_form_builder(caller, "write")

    form = forms_service.publish_form(caller, form_id)

    return serializers.form_detail_to_dict(form), 200


def archive_form(form_id):
    caller = current_caller()
    require_form_builder(caller, "write")

    form = forms_service.archive_form(caller, form_id)

    return serializers.form_detail_to_dict(form), 200


def list_versions(form_id):
    require_form_builder(current_caller(), "read")

    form = forms_service.get_form(form_id)

    return [serializers.version_summary_to_dict(version) for version in form.versions], 200


def get_version(form_id, version_no):
    require_appraisal_service_or_form_reader(current_caller())

    return serializers.version_to_dict(forms_service.get_version(form_id, version_no)), 200


def set_assignments(form_id, body):
    caller = current_caller()
    require_form_builder(caller, "write")

    form = forms_service.set_assignments(
        caller, form_id, body.get("project_id"), body.get("role_type_codes")
    )

    return serializers.form_detail_to_dict(form), 200


def resolve_form(project_id, role_type, purpose="appraisal"):
    require_appraisal_service_or_form_reader(current_caller())

    version = forms_service.resolve_form(project_id, role_type, purpose)

    return serializers.resolved_form_to_dict(version), 200
