"""Form builder use cases: drafting, publishing, archiving, assigning and resolving forms."""

import copy

from sqlalchemy.exc import IntegrityError

import common.errors as errors
from clients import directory_client
from common.hph_logging import logging
from models import (
    ASSIGNABLE_ROLE_TYPES,
    Form,
    FormAssignment,
    FormEvent,
    FormPurpose,
    FormStatus,
    FormTemplate,
    FormVersion,
    db,
    utc_now,
)
from services.definition import validate_answers, validate_definition


logger = logging.getLogger(__name__)

MAX_NAME_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 2000
UPDATABLE_FIELDS = ("name", "description", "definition")


def _clean_name(value):
    if not isinstance(value, str) or not value.strip():
        raise errors.InvalidArgument("name is required")

    value = value.strip()
    if len(value) > MAX_NAME_LENGTH:
        raise errors.InvalidArgument(f"name must be at most {MAX_NAME_LENGTH} characters")

    return value


def _clean_description(value):
    if value is None:
        return None

    if not isinstance(value, str):
        raise errors.InvalidArgument("description must be a string")

    value = value.strip()
    if len(value) > MAX_DESCRIPTION_LENGTH:
        raise errors.InvalidArgument(
            f"description must be at most {MAX_DESCRIPTION_LENGTH} characters"
        )

    return value or None


def _clean_purpose(value):
    if value not in FormPurpose.ALL:
        raise errors.InvalidArgument(f"purpose must be one of {', '.join(FormPurpose.ALL)}")

    return value


def _record(form_id, actor_user_id, action, details=None):
    db.session.add(
        FormEvent(form_id=form_id, actor_user_id=actor_user_id, action=action, details=details)
    )


def _commit_or_conflict(message):
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise errors.AlreadyExists(message) from exc


def get_form(form_id):
    form = db.session.get(Form, form_id)
    if form is None:
        raise errors.NotFound(f"form {form_id} not found")

    return form


def list_forms(status=None, project_id=None, role_type=None):
    """Return forms, newest change first, optionally filtered by status or active assignment."""
    query = db.session.query(Form)

    if status is not None:
        if status not in FormStatus.ALL:
            raise errors.InvalidArgument(f"status must be one of {', '.join(FormStatus.ALL)}")
        query = query.filter(Form.status == status)

    if project_id is not None or role_type is not None:
        query = query.join(FormAssignment).filter(FormAssignment.active.is_(True))
        if project_id is not None:
            query = query.filter(FormAssignment.project_id == project_id)
        if role_type is not None:
            query = query.filter(FormAssignment.role_type_code == role_type)

    return query.distinct().order_by(Form.updated_at.desc(), Form.id.desc()).all()


def create_form(
    caller,
    name,
    description=None,
    purpose=FormPurpose.APPRAISAL,
    from_template_id=None,
    definition=None,
):
    """Create a draft form, blank, from a template or from a supplied definition.

    Raises
    ------
    common.errors.InvalidArgument
        If the name or purpose is invalid, or both a template and a definition are given.
    common.errors.NotFound
        If the template does not exist.
    """
    name = _clean_name(name)
    description = _clean_description(description)
    purpose = _clean_purpose(purpose)

    if from_template_id is not None and definition is not None:
        raise errors.InvalidArgument("send either from_template_id or definition, not both")

    if from_template_id is not None:
        template = db.session.get(FormTemplate, from_template_id)
        if template is None:
            raise errors.NotFound(f"template {from_template_id} not found")
        if template.purpose != purpose:
            raise errors.InvalidArgument(f"template {from_template_id} is not a {purpose} template")
        draft = copy.deepcopy(template.definition)
    elif definition is not None:
        if not isinstance(definition, dict):
            raise errors.InvalidArgument("definition must be an object")
        draft = definition
    else:
        draft = {"sections": []}

    form = Form(
        name=name,
        description=description,
        purpose=purpose,
        status=FormStatus.DRAFT,
        draft_definition=draft,
        created_by=caller.user_id,
        updated_by=caller.user_id,
    )
    db.session.add(form)
    db.session.flush()

    _record(form.id, caller.user_id, "created", {"from_template_id": from_template_id})
    db.session.commit()

    return form


def update_form(caller, form_id, changes):
    """Apply a partial update of name, description and/or the draft definition.

    Raises
    ------
    common.errors.FailedPrecondition
        If the form is archived.
    common.errors.InvalidArgument
        If a value is invalid or nothing updatable was sent.
    """
    form = get_form(form_id)
    if form.status == FormStatus.ARCHIVED:
        raise errors.FailedPrecondition("archived forms cannot be edited")

    present = [field for field in UPDATABLE_FIELDS if field in changes]
    if not present:
        raise errors.InvalidArgument(f"send at least one of {', '.join(UPDATABLE_FIELDS)}")

    if "name" in changes:
        form.name = _clean_name(changes["name"])
    if "description" in changes:
        form.description = _clean_description(changes["description"])
    if "definition" in changes:
        if not isinstance(changes["definition"], dict):
            raise errors.InvalidArgument("definition must be an object")
        form.draft_definition = changes["definition"]

    form.updated_by = caller.user_id
    _record(form.id, caller.user_id, "updated", {"fields": present})
    db.session.commit()

    return form


def delete_form(caller, form_id):
    """Delete a form that was never published.

    Raises
    ------
    common.errors.FailedPrecondition
        If the form has published versions; those must stay for the appraisals that pin them.
    """
    form = get_form(form_id)
    if form.versions:
        raise errors.FailedPrecondition("this form has published versions; archive it instead")

    _record(None, caller.user_id, "deleted", {"form_id": form.id, "name": form.name})
    db.session.delete(form)
    db.session.commit()


def publish_form(caller, form_id):
    """Freeze the current draft as the next version (FR-9).

    Raises
    ------
    common.errors.InvalidArgument
        If the draft has validation errors; they are listed in ``data.errors``.
    common.errors.FailedPrecondition
        If the form is archived or the draft is identical to the latest version.
    """
    form = get_form(form_id)
    if form.status == FormStatus.ARCHIVED:
        raise errors.FailedPrecondition("archived forms cannot be published")

    normalised, problems = validate_definition(form.draft_definition)
    if problems:
        raise errors.InvalidArgument(
            "the draft has errors; fix them before publishing", data={"errors": problems}
        )

    latest = form.latest_version
    if latest is not None and latest.definition == normalised:
        raise errors.FailedPrecondition(f"nothing changed since version {latest.version_no}")

    version = FormVersion(
        form=form,
        version_no=latest.version_no + 1 if latest else 1,
        definition=normalised,
        published_by=caller.user_id,
    )
    db.session.add(version)

    form.status = FormStatus.PUBLISHED
    form.draft_definition = normalised
    form.updated_by = caller.user_id
    _record(form.id, caller.user_id, "published", {"version_no": version.version_no})

    _commit_or_conflict("the form was published at the same time by someone else; reload it")

    return form


def archive_form(caller, form_id):
    """Archive a form and deactivate its assignments; pinned versions stay readable."""
    form = get_form(form_id)
    if form.status == FormStatus.ARCHIVED:
        raise errors.FailedPrecondition("form is already archived")

    now = utc_now()
    for assignment in form.active_assignments:
        assignment.active = False
        assignment.deactivated_at = now

    form.status = FormStatus.ARCHIVED
    form.updated_by = caller.user_id
    _record(form.id, caller.user_id, "archived")
    db.session.commit()

    return form


def get_version(form_id, version_no):
    version = (
        db.session.query(FormVersion)
        .filter(FormVersion.form_id == form_id, FormVersion.version_no == version_no)
        .one_or_none()
    )
    if version is None:
        raise errors.NotFound(f"form {form_id} has no version {version_no}")

    return version


def get_version_by_id(version_id):
    version = db.session.get(FormVersion, version_id)
    if version is None:
        raise errors.NotFound(f"form version {version_id} not found")

    return version


def set_assignments(caller, form_id, project_id, role_type_codes):
    """Make the form apply to one project for the given role types (FR-8).

    An empty ``role_type_codes`` removes every assignment of the form.

    Raises
    ------
    common.errors.InvalidArgument
        If a role type cannot take this form or the project does not exist.
    common.errors.AlreadyExists
        If another form is already assigned to the same project and role type.
    common.errors.FailedPrecondition
        If the form is archived.
    """
    form = get_form(form_id)
    if form.status == FormStatus.ARCHIVED:
        raise errors.FailedPrecondition("archived forms cannot be assigned")

    if not isinstance(role_type_codes, list) or not all(
        isinstance(code, str) for code in role_type_codes
    ):
        raise errors.InvalidArgument("role_type_codes must be a list of role type codes")

    codes = sorted(set(role_type_codes))
    allowed = ASSIGNABLE_ROLE_TYPES.get(form.purpose, ())
    invalid = [code for code in codes if code not in allowed]
    if invalid:
        raise errors.InvalidArgument(
            f"{', '.join(invalid)} cannot take a {form.purpose} form; allowed: {', '.join(allowed)}"
        )

    if codes:
        if project_id is None:
            raise errors.InvalidArgument("project_id is required when assigning role types")
        if not directory_client().projects(ids=[project_id]):
            raise errors.InvalidArgument(f"project {project_id} does not exist")

        conflicts = (
            db.session.query(FormAssignment)
            .filter(
                FormAssignment.active.is_(True),
                FormAssignment.purpose == form.purpose,
                FormAssignment.project_id == project_id,
                FormAssignment.role_type_code.in_(codes),
                FormAssignment.form_id != form.id,
            )
            .all()
        )
        if conflicts:
            described = "; ".join(
                f"{item.role_type_code} already uses form '{item.form.name}' (id {item.form_id})"
                for item in conflicts
            )
            raise errors.AlreadyExists(
                f"project {project_id}: {described}; remove that assignment first",
                data={
                    "conflicts": [
                        {"form_id": item.form_id, "role_type_code": item.role_type_code}
                        for item in conflicts
                    ]
                },
            )

    desired = {(project_id, code) for code in codes}
    now = utc_now()
    for assignment in form.active_assignments:
        if (assignment.project_id, assignment.role_type_code) not in desired:
            assignment.active = False
            assignment.deactivated_at = now
    db.session.flush()

    current = {(item.project_id, item.role_type_code) for item in form.active_assignments}
    for target_project_id, code in sorted(desired - current):
        db.session.add(
            FormAssignment(
                form=form,
                purpose=form.purpose,
                project_id=target_project_id,
                role_type_code=code,
                active=True,
                created_by=caller.user_id,
            )
        )

    form.updated_by = caller.user_id
    _record(
        form.id,
        caller.user_id,
        "assignments_updated",
        {"project_id": project_id, "role_type_codes": codes},
    )
    _commit_or_conflict("another form was assigned to the same target at the same time; reload")

    return form


def resolve_form(project_id, role_type, purpose=FormPurpose.APPRAISAL):
    """Return the published version that applies to a project and role type.

    Raises
    ------
    common.errors.NotFound
        If no published form is actively assigned to that target.
    """
    _clean_purpose(purpose)

    assignment = (
        db.session.query(FormAssignment)
        .join(Form)
        .filter(
            FormAssignment.active.is_(True),
            FormAssignment.project_id == project_id,
            FormAssignment.role_type_code == role_type,
            FormAssignment.purpose == purpose,
            Form.status == FormStatus.PUBLISHED,
        )
        .one_or_none()
    )
    if assignment is None or assignment.form.latest_version is None:
        raise errors.NotFound(
            f"no published {purpose} form is assigned to {role_type} in project {project_id}"
        )

    return assignment.form.latest_version


def validate_version_answers(version_id, answers, stage, mode):
    """Validate answers against a pinned version; returns ``(normalised, errors)``."""
    version = get_version_by_id(version_id)

    return validate_answers(version.definition, answers, stage, mode)


def list_templates(purpose=None):
    query = db.session.query(FormTemplate)
    if purpose is not None:
        query = query.filter(FormTemplate.purpose == _clean_purpose(purpose))

    return query.order_by(FormTemplate.is_default.desc(), FormTemplate.name).all()
