"""Handlers for /form-templates."""

from common.connexion_app import current_caller
from services import forms as forms_service, serializers
from services.permissions import require_form_builder


def list_templates(purpose=None):
    require_form_builder(current_caller(), "read")

    templates = forms_service.list_templates(purpose)

    return [serializers.template_to_dict(template) for template in templates], 200
