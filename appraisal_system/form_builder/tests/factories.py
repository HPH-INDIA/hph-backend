import copy

import factory

from common.configdb import get_sql_alchemy
from models import Form, FormAssignment, FormStatus, FormTemplate, FormVersion


def _factory_session():
    return get_sql_alchemy().session


class BaseModelFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        abstract = True
        sqlalchemy_session_persistence = "commit"

    @classmethod
    def _build(cls, model_class, *args, **kwargs):
        cls._meta.sqlalchemy_session = _factory_session()
        return super()._build(model_class, *args, **kwargs)

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        cls._meta.sqlalchemy_session = _factory_session()
        return super()._create(model_class, *args, **kwargs)


# Already normalised, so publishing it unchanged produces exactly this definition.
VALID_DEFINITION = {
    "sections": [
        {
            "key": "self_assessment",
            "title": "Self-assessment",
            "filled_by": "employee",
            "fields": [
                {
                    "key": "achievements",
                    "type": "long_text",
                    "label": "Key achievements",
                    "required": True,
                    "max_length": 4000,
                },
                {
                    "key": "self_rating",
                    "type": "rating",
                    "label": "Self rating",
                    "required": True,
                    "scale": {"min": 1, "max": 5},
                },
            ],
        },
        {
            "key": "lead_assessment",
            "title": "Lead review",
            "filled_by": "reviewer",
            "review_stage": "lead",
            "fields": [
                {
                    "key": "lead_comments",
                    "type": "long_text",
                    "label": "Lead comments",
                    "required": True,
                    "max_length": 4000,
                }
            ],
        },
        {
            "key": "manager_assessment",
            "title": "Manager review",
            "filled_by": "reviewer",
            "review_stage": "manager",
            "fields": [
                {
                    "key": "manager_rating",
                    "type": "rating",
                    "label": "Final rating",
                    "required": True,
                    "scale": {"min": 1, "max": 5},
                }
            ],
        },
    ]
}


def valid_definition():
    return copy.deepcopy(VALID_DEFINITION)


class FormFactory(BaseModelFactory):
    class Meta:
        model = Form

    name = factory.Sequence(lambda n: f"Form {n}")
    description = None
    purpose = "appraisal"
    status = FormStatus.DRAFT
    draft_definition = factory.LazyFunction(valid_definition)
    created_by = 1
    updated_by = 1


class FormVersionFactory(BaseModelFactory):
    class Meta:
        model = FormVersion

    form = factory.SubFactory(FormFactory, status=FormStatus.PUBLISHED)
    version_no = 1
    definition = factory.LazyFunction(valid_definition)
    published_by = 1


class FormAssignmentFactory(BaseModelFactory):
    class Meta:
        model = FormAssignment

    form = factory.SubFactory(FormFactory)
    purpose = "appraisal"
    project_id = 1
    role_type_code = "employee"
    active = True
    created_by = 1


class FormTemplateFactory(BaseModelFactory):
    class Meta:
        model = FormTemplate

    name = factory.Sequence(lambda n: f"Template {n}")
    description = "A template"
    purpose = "appraisal"
    definition = factory.LazyFunction(valid_definition)
    is_default = True


def published_form(project_id=1, role_type_code="employee", definition=None, **form_values):
    """Create a published form with version 1 and an active assignment."""
    definition = definition or valid_definition()
    form = FormFactory(status=FormStatus.PUBLISHED, draft_definition=definition, **form_values)
    FormVersionFactory(form=form, version_no=1, definition=definition)
    if project_id is not None:
        FormAssignmentFactory(form=form, project_id=project_id, role_type_code=role_type_code)

    return form
