"""Map form builder models to the response shapes in ``form_builder.yaml``."""

from services.definition import validate_definition


def _timestamp(value):
    return value.isoformat() if value else None


def assignment_to_dict(assignment):
    return {
        "project_id": assignment.project_id,
        "role_type_code": assignment.role_type_code,
        "active": assignment.active,
    }


def version_summary_to_dict(version):
    return {
        "id": version.id,
        "form_id": version.form_id,
        "version_no": version.version_no,
        "published_by": version.published_by,
        "published_at": _timestamp(version.published_at),
    }


def version_to_dict(version):
    payload = version_summary_to_dict(version)
    payload["form_name"] = version.form.name
    payload["purpose"] = version.form.purpose
    payload["definition"] = version.definition
    return payload


def form_summary_to_dict(form):
    latest = form.latest_version
    normalised, _ = validate_definition(form.draft_definition)

    return {
        "id": form.id,
        "name": form.name,
        "description": form.description,
        "purpose": form.purpose,
        "status": form.status,
        "latest_version": version_summary_to_dict(latest) if latest else None,
        # Lets the builder flag a published form whose draft has moved on.
        "has_unpublished_changes": latest is None or normalised != latest.definition,
        "assignments": [assignment_to_dict(item) for item in form.active_assignments],
        "created_by": form.created_by,
        "updated_by": form.updated_by,
        "created_at": _timestamp(form.created_at),
        "updated_at": _timestamp(form.updated_at),
    }


def form_detail_to_dict(form):
    payload = form_summary_to_dict(form)
    _, draft_errors = validate_definition(form.draft_definition)
    payload["draft_definition"] = form.draft_definition
    payload["draft_errors"] = draft_errors
    payload["version_count"] = len(form.versions)
    return payload


def resolved_form_to_dict(version):
    return {
        "form_id": version.form_id,
        "form_name": version.form.name,
        "version_id": version.id,
        "version_no": version.version_no,
        "definition": version.definition,
    }


def template_to_dict(template):
    return {
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "purpose": template.purpose,
        "is_default": template.is_default,
        "definition": template.definition,
    }
