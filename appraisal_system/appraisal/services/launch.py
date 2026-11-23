"""Launching a cycle (FR-3, §10.2, §10.6): who is appraised, with which form, reviewed by whom."""

from dataclasses import dataclass

import common.errors as errors
from clients import directory_client, forms_client
from models import (
    Appraisal,
    AppraisalStatus,
    CycleScope,
    CycleStatus,
    NotificationType,
    db,
    utc_now,
)
from services import notifications
from services.audit import record_event
from services.cycles import get_cycle
from services.permissions import CYCLE_ADMIN_FEATURE, require_feature


# v1 appraises Employees and Leads only; Managers review, admins monitor.
APPRAISED_ROLE_TYPES = ("employee", "lead")


@dataclass
class ReviewChain:
    """The reviewers resolved for one participant; ``problem`` is set when it is incomplete."""

    lead: object = None
    manager: object = None
    problem: str = None


def resolve_review_chain(participant, users_by_id):
    """Find the Lead and Manager who review *participant* (§10.2).

    An Employee's appraisal goes Employee -> Lead -> Manager, a Lead's goes Lead -> Manager.
    Employees reporting straight to a Manager are not supported in v1.

    Parameters
    ----------
    participant : common.directory_client.DirectoryUser
    users_by_id : dict of int to DirectoryUser
        Directory entries for everyone in the participant's reporting line.

    Returns
    -------
    ReviewChain
    """

    def active_with_role(user_id, role_type):
        user = users_by_id.get(user_id) if user_id is not None else None
        if user is None or not user.is_active or user.role_type_code != role_type:
            return None
        if user.id == participant.id:
            return None
        return user

    if participant.role_type_code == "lead":
        manager = active_with_role(participant.reports_to_id, "manager")
        if manager is None:
            return ReviewChain(problem="no active manager in the lead's reporting line")
        return ReviewChain(manager=manager)

    lead = active_with_role(participant.reports_to_id, "lead")
    if lead is None:
        return ReviewChain(problem="no active lead in the employee's reporting line")

    manager = active_with_role(lead.reports_to_id, "manager")
    if manager is None:
        return ReviewChain(lead=lead, problem="the employee's lead has no active manager")

    return ReviewChain(lead=lead, manager=manager)


def _participants(cycle):
    directory = directory_client()
    project_id = cycle.project_id if cycle.scope == CycleScope.PROJECT else None
    users = directory.users(project_id=project_id, role_types=APPRAISED_ROLE_TYPES, active=True)

    # People who left before the period started are not appraised for it.
    return [
        user
        for user in users
        if user.last_working_day is None or user.last_working_day >= cycle.start_date
    ]


def _reporting_lines(participants):
    """Fetch everyone up to two levels above the participants in two batched calls."""
    directory = directory_client()
    users_by_id = {user.id: user for user in participants}

    supervisor_ids = {user.reports_to_id for user in participants if user.reports_to_id}
    missing = supervisor_ids - set(users_by_id)
    for user in directory.users(ids=missing):
        users_by_id[user.id] = user

    second_level_ids = {
        users_by_id[user_id].reports_to_id
        for user_id in supervisor_ids
        if user_id in users_by_id and users_by_id[user_id].reports_to_id
    }
    missing = second_level_ids - set(users_by_id)
    for user in directory.users(ids=missing):
        users_by_id[user.id] = user

    return users_by_id


def launch_cycle(caller, cycle_id):
    """Create an appraisal for every Employee and Lead in scope who has a published form.

    Launching again adds people who joined since (idempotent per person). Nothing is saved if
    the directory or Form Builder cannot be reached.

    Returns
    -------
    dict
        ``{"created", "needs_reviewer", "skipped": [{user_id, name, reason}]}`` for this run.

    Raises
    ------
    common.errors.FailedPrecondition
        If the cycle is closed.
    common.errors.Unavailable
        If a service this depends on cannot be reached.
    """
    require_feature(caller, CYCLE_ADMIN_FEATURE, "write")
    cycle = get_cycle(cycle_id)

    if cycle.status == CycleStatus.CLOSED:
        raise errors.FailedPrecondition("closed cycles cannot be launched")

    participants = _participants(cycle)
    already_in = {
        user_id
        for (user_id,) in db.session.query(Appraisal.user_id).filter(Appraisal.cycle_id == cycle.id)
    }
    newcomers = [user for user in participants if user.id not in already_in]
    users_by_id = _reporting_lines(newcomers)

    forms = forms_client()
    resolved_forms = {}
    skipped = []
    created = []

    for user in sorted(newcomers, key=lambda item: item.id):
        if user.project_id is None:
            skipped.append({"user_id": user.id, "name": user.full_name, "reason": "has no project"})
            continue

        form_key = (user.project_id, user.role_type_code)
        if form_key not in resolved_forms:
            resolved_forms[form_key] = forms.resolve(user.project_id, user.role_type_code)
        form = resolved_forms[form_key]
        if form is None:
            skipped.append(
                {
                    "user_id": user.id,
                    "name": user.full_name,
                    "reason": f"no published form for {user.role_type_code} in project "
                    f"{user.project_id}",
                }
            )
            continue

        chain = resolve_review_chain(user, users_by_id)
        appraisal = Appraisal(
            cycle=cycle,
            user_id=user.id,
            employee_name=user.full_name,
            employee_emp_id=user.emp_id,
            project_id=user.project_id,
            role_type_code=user.role_type_code,
            form_id=form["form_id"],
            form_version_id=form["version_id"],
            form_name=form["form_name"],
            form_definition=form["definition"],
            lead_user_id=chain.lead.id if chain.lead else None,
            lead_name=chain.lead.full_name if chain.lead else None,
            manager_user_id=chain.manager.id if chain.manager else None,
            manager_name=chain.manager.full_name if chain.manager else None,
            status=AppraisalStatus.NEEDS_REVIEWER if chain.problem else AppraisalStatus.NOT_STARTED,
            needs_reviewer_reason=chain.problem,
            review_attempt=1,
        )
        db.session.add(appraisal)
        created.append(appraisal)

    db.session.flush()

    for appraisal in created:
        record_event(
            cycle.id,
            caller.user_id,
            "appraisal_created",
            appraisal=appraisal,
            to_status=appraisal.status,
            details={"form_version_id": appraisal.form_version_id},
        )
        notifications.enqueue(NotificationType.APPRAISAL_OPENED, appraisal.user_id, appraisal)

    was_draft = cycle.status == CycleStatus.DRAFT
    if was_draft:
        cycle.status = CycleStatus.ACTIVE
        cycle.launched_by = caller.user_id
        cycle.launched_at = utc_now()

    summary = {
        "created": len(created),
        "needs_reviewer": sum(
            1 for item in created if item.status == AppraisalStatus.NEEDS_REVIEWER
        ),
        "skipped": skipped,
    }
    # Keep the latest skip list; people skipped earlier and since fixed are no longer listed.
    cycle.launch_summary = {**summary, "launched_at": utc_now().isoformat()}
    record_event(
        cycle.id,
        caller.user_id,
        "cycle_launched" if was_draft else "cycle_relaunched",
        from_status=CycleStatus.DRAFT if was_draft else CycleStatus.ACTIVE,
        to_status=CycleStatus.ACTIVE,
        details=summary,
    )
    db.session.commit()

    return summary
