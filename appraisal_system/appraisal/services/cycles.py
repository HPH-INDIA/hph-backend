"""Appraisal cycles (FR-1, FR-2, FR-4, FR-15): set-up, closing and progress."""

from collections import Counter
from datetime import date

import common.errors as errors
from clients import directory_client
from models import (
    Appraisal,
    AppraisalStatus,
    Cycle,
    CycleFrequency,
    CycleScope,
    CycleStatus,
    db,
    utc_now,
)
from services.audit import record_event
from services.permissions import (
    CYCLE_ADMIN_FEATURE,
    is_cycle_admin,
    is_reviewer,
    require_feature,
    require_user,
)


MAX_NAME_LENGTH = 200
# What an admin may still change once a cycle is running.
ACTIVE_EDITABLE_FIELDS = ("name", "submission_due", "review_due")
DRAFT_EDITABLE_FIELDS = (
    "name",
    "scope",
    "project_id",
    "frequency",
    "start_date",
    "end_date",
    "submission_due",
    "review_due",
    "require_one_to_one",
)


def _parse_date(value, name):
    if isinstance(value, date):
        return value

    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise errors.InvalidArgument(f"{name} must be a date as YYYY-MM-DD") from exc


def _clean_name(value):
    if not isinstance(value, str) or not value.strip():
        raise errors.InvalidArgument("name is required")

    value = value.strip()
    if len(value) > MAX_NAME_LENGTH:
        raise errors.InvalidArgument(f"name must be at most {MAX_NAME_LENGTH} characters")

    return value


def _apply(cycle, values):
    """Copy validated values onto *cycle*; dates arrive as ISO strings."""
    if "name" in values:
        cycle.name = _clean_name(values["name"])
    if "scope" in values:
        if values["scope"] not in CycleScope.ALL:
            raise errors.InvalidArgument(f"scope must be one of {', '.join(CycleScope.ALL)}")
        cycle.scope = values["scope"]
    if "project_id" in values:
        cycle.project_id = values["project_id"]
    if "frequency" in values:
        if values["frequency"] not in CycleFrequency.ALL:
            raise errors.InvalidArgument(
                f"frequency must be one of {', '.join(CycleFrequency.ALL)}"
            )
        cycle.frequency = values["frequency"]
    for name in ("start_date", "end_date", "submission_due", "review_due"):
        if name in values:
            setattr(cycle, name, _parse_date(values[name], name))
    if "require_one_to_one" in values:
        cycle.require_one_to_one = bool(values["require_one_to_one"])


def _check_consistency(cycle):
    if cycle.scope == CycleScope.PROJECT and cycle.project_id is None:
        raise errors.InvalidArgument("project_id is required for a project cycle")
    if cycle.scope == CycleScope.ORGANIZATION and cycle.project_id is not None:
        raise errors.InvalidArgument("an organization cycle must not have a project_id")
    if cycle.end_date < cycle.start_date:
        raise errors.InvalidArgument("end_date must not be before start_date")
    if cycle.submission_due < cycle.start_date:
        raise errors.InvalidArgument("submission_due must not be before start_date")
    if cycle.review_due < cycle.submission_due:
        raise errors.InvalidArgument("review_due must not be before submission_due")


def _check_project_exists(project_id):
    if project_id is not None and not directory_client().projects(ids=[project_id]):
        raise errors.InvalidArgument(f"project {project_id} does not exist")


def get_cycle(cycle_id):
    cycle = db.session.get(Cycle, cycle_id)
    if cycle is None:
        raise errors.NotFound(f"cycle {cycle_id} not found")

    return cycle


def get_visible_cycle(caller, cycle_id):
    """Return a cycle admins can see, or a launched one reviewers can see."""
    require_user(caller)
    cycle = get_cycle(cycle_id)

    if is_cycle_admin(caller):
        return cycle
    if is_reviewer(caller) and cycle.status != CycleStatus.DRAFT:
        return cycle

    raise errors.PermissionDenied(f"requires the {CYCLE_ADMIN_FEATURE} feature (read)")


def list_cycles(caller, status=None):
    """Admins see every cycle; Leads and Managers see launched ones (for progress)."""
    require_user(caller)
    if not is_cycle_admin(caller) and not is_reviewer(caller):
        raise errors.PermissionDenied(f"requires the {CYCLE_ADMIN_FEATURE} feature (read)")

    query = db.session.query(Cycle)
    if status is not None:
        query = query.filter(Cycle.status == status)
    if not is_cycle_admin(caller):
        query = query.filter(Cycle.status != CycleStatus.DRAFT)

    return query.order_by(Cycle.start_date.desc(), Cycle.id.desc()).all()


def create_cycle(caller, values):
    """Create a draft cycle.

    Raises
    ------
    common.errors.InvalidArgument
        If the dates are inconsistent, the scope and project disagree, or the project is unknown.
    """
    require_feature(caller, CYCLE_ADMIN_FEATURE, "write")

    cycle = Cycle(
        status=CycleStatus.DRAFT,
        frequency=CycleFrequency.ANNUAL,
        require_one_to_one=True,
        project_id=None,
        created_by=caller.user_id,
    )
    _apply(cycle, values)
    _check_consistency(cycle)
    _check_project_exists(cycle.project_id)

    db.session.add(cycle)
    db.session.flush()
    record_event(cycle.id, caller.user_id, "cycle_created", to_status=CycleStatus.DRAFT)
    db.session.commit()

    return cycle


def update_cycle(caller, cycle_id, values):
    """Edit a draft cycle freely, or only its name and due dates once it is active.

    Raises
    ------
    common.errors.FailedPrecondition
        If the cycle is closed, or a field that is locked after launch is sent.
    """
    require_feature(caller, CYCLE_ADMIN_FEATURE, "write")
    cycle = get_cycle(cycle_id)

    if cycle.status == CycleStatus.CLOSED:
        raise errors.FailedPrecondition("closed cycles cannot be edited")

    allowed = DRAFT_EDITABLE_FIELDS if cycle.status == CycleStatus.DRAFT else ACTIVE_EDITABLE_FIELDS
    locked = sorted(set(values) - set(allowed))
    if locked:
        raise errors.FailedPrecondition(
            f"{', '.join(locked)} cannot change once the cycle is {cycle.status}"
        )

    project_changed = "project_id" in values and values["project_id"] != cycle.project_id
    _apply(cycle, values)
    _check_consistency(cycle)
    if project_changed:
        _check_project_exists(cycle.project_id)

    record_event(cycle.id, caller.user_id, "cycle_updated", details={"fields": sorted(values)})
    db.session.commit()

    return cycle


def close_cycle(caller, cycle_id):
    """Close an active cycle; every appraisal in it becomes read-only (FR-4)."""
    require_feature(caller, CYCLE_ADMIN_FEATURE, "write")
    cycle = get_cycle(cycle_id)

    if cycle.status != CycleStatus.ACTIVE:
        raise errors.FailedPrecondition(
            f"only active cycles can be closed; this one is {cycle.status}"
        )

    cycle.status = CycleStatus.CLOSED
    cycle.closed_by = caller.user_id
    cycle.closed_at = utc_now()
    record_event(
        cycle.id,
        caller.user_id,
        "cycle_closed",
        from_status=CycleStatus.ACTIVE,
        to_status=CycleStatus.CLOSED,
    )
    db.session.commit()

    return cycle


def cycle_progress(caller, cycle_id):
    """Count appraisals by status, review level and project (FR-15).

    Admins see the whole cycle; Leads and Managers see only appraisals they review.

    Returns
    -------
    dict
    """
    cycle = get_visible_cycle(caller, cycle_id)
    admin = is_cycle_admin(caller)

    query = db.session.query(Appraisal).filter(Appraisal.cycle_id == cycle.id)
    if not admin:
        query = query.filter(
            (Appraisal.lead_user_id == caller.user_id)
            | (Appraisal.manager_user_id == caller.user_id)
        )
    appraisals = query.all()

    by_status = Counter(appraisal.status for appraisal in appraisals)
    awaiting_by_stage = Counter(
        appraisal.current_step.review_stage
        for appraisal in appraisals
        if appraisal.status == AppraisalStatus.AWAITING_REVIEW and appraisal.current_step
    )

    projects = {}
    for appraisal in appraisals:
        bucket = projects.setdefault(appraisal.project_id, Counter())
        bucket[appraisal.status] += 1

    progress = {
        "cycle_id": cycle.id,
        "total": len(appraisals),
        "by_status": {status: by_status.get(status, 0) for status in AppraisalStatus.ALL},
        "awaiting_review_by_stage": {
            "lead": awaiting_by_stage.get("lead", 0),
            "manager": awaiting_by_stage.get("manager", 0),
        },
        "by_project": [
            {"project_id": project_id, "total": sum(counts.values()), "by_status": dict(counts)}
            for project_id, counts in sorted(projects.items(), key=lambda item: item[0] or 0)
        ],
        "needs_reviewer": [
            {
                "appraisal_id": appraisal.id,
                "user_id": appraisal.user_id,
                "employee_name": appraisal.employee_name,
                "reason": appraisal.needs_reviewer_reason,
            }
            for appraisal in appraisals
            if appraisal.status == AppraisalStatus.NEEDS_REVIEWER
        ],
    }
    if admin:
        progress["skipped_at_launch"] = (cycle.launch_summary or {}).get("skipped", [])

    return progress
