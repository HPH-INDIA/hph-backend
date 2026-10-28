"""Handlers for /form-versions; used by the appraisal service to read and check pinned forms."""

from common.connexion_app import current_caller
from services import forms as forms_service, serializers
from services.permissions import require_appraisal_service_or_form_reader


def get_version_by_id(version_id):
    require_appraisal_service_or_form_reader(current_caller())

    return serializers.version_to_dict(forms_service.get_version_by_id(version_id)), 200


def validate_answers(version_id, body):
    require_appraisal_service_or_form_reader(current_caller())

    normalised, problems = forms_service.validate_version_answers(
        version_id, body.get("answers"), body.get("stage"), body.get("mode")
    )

    return {"valid": not problems, "errors": problems, "normalized_answers": normalised}, 200
