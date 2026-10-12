"""Reviewing an appraisal (FR-11 to FR-13): Lead hand-off, one-to-one, send back, approve."""

from datetime import datetime, timedelta, timezone

import common.errors as errors
from clients import directory_client
from models import (
    Answer,
    Appraisal,
    AppraisalStatus,
    CommentKind,
    Cycle,
    CycleStatus,
    NotificationType,
    ReviewComment,
    ReviewStage,
    ReviewStep,
    StepStatus,
    db,
    utc_now,
)
from services import notifications
from services.appraisals import (
    MODE_SUBMIT,
    answers_for,
    get_appraisal,
    require_open_cycle,
    validated,
)
from services.audit import record_event
from services.permissions import (
    CYCLE_ADMIN_FEATURE,
    REVIEW_FEATURE,
    require_feature,
    require_user,
)


MAX_TEXT_LENGTH = 5000
# One-to-ones are recorded after they happen; allow small clock differences only.
ONE_TO_ONE_FUTURE_TOLERANCE = timedelta(minutes=5)


def _clean_text(value, name, required):
    if value is None:
        if required:
            raise errors.InvalidArgument(f"{name} is required")
        return None

    if not isinstance(value, str):
        raise errors.InvalidArgument(f"{name} must be text")

    value = value.strip()
    if not value:
        if required:
            raise errors.InvalidArgument(f"{name} is required")
        return None
    if len(value) > MAX_TEXT_LENGTH:
        raise errors.InvalidArgument(f"{name} must be at most {MAX_TEXT_LENGTH} characters")

    return value


def _active_step(caller, appraisal, stage=None):
    """Return the caller's active step, enforcing that it is their turn (and the right stage)."""
    require_feature(caller, REVIEW_FEATURE, "write")
    require_open_cycle(appraisal)

    if appraisal.status != AppraisalStatus.AWAITING_REVIEW:
        raise errors.FailedPrecondition(
            f"this appraisal is {appraisal.status}, not awaiting review"
        )

    step = appraisal.current_step
    if step is None or step.reviewer_user_id != caller.user_id:
        raise errors.PermissionDenied("you are not the current reviewer of this appraisal")
    if stage is not None and step.review_stage != stage:
        raise errors.FailedPrecondition(
            f"this action belongs to the {stage} step; the current step is {step.review_stage}"
        )

    return step


def _add_comment(appraisal, step, kind, body):
    db.session.add(
        ReviewComment(
            appraisal_id=appraisal.id,
            review_step_id=step.id,
            review_attempt=appraisal.review_attempt,
            review_stage=step.review_stage,
            author_user_id=step.reviewer_user_id,
            author_name=step.reviewer_name,
            kind=kind,
            body=body,
        )
    )


def inbox(caller, status="active", cycle_id=None):
    """Return ``(step, appraisal)`` pairs assigned to the caller (FR-12).

    ``active`` lists what is waiting on them now; ``completed`` lists what they finished or sent
    back.
    """
    require_feature(caller, REVIEW_FEATURE, "read")

    statuses = (
        (StepStatus.ACTIVE,) if status == "active" else (StepStatus.COMPLETED, StepStatus.RETURNED)
    )
    query = (
        db.session.query(ReviewStep, Appraisal)
        .join(Appraisal, ReviewStep.appraisal_id == Appraisal.id)
        .join(Cycle, Appraisal.cycle_id == Cycle.id)
        .filter(ReviewStep.reviewer_user_id == caller.user_id, ReviewStep.status.in_(statuses))
    )
    if status == "active":
        query = query.filter(Cycle.status == CycleStatus.ACTIVE)
    if cycle_id is not None:
        query = query.filter(Appraisal.cycle_id == cycle_id)

    order = ReviewStep.activated_at if status == "active" else ReviewStep.completed_at.desc()

    return query.order_by(order, ReviewStep.id).all()


def complete_lead_review(caller, appraisal_id, comment=None):
    """Finish the Lead step and hand the appraisal to the Manager (FR-12).

    This is a hand-off, not an approval; only the Manager can approve.
    """
    require_user(caller)
    appraisal = get_appraisal(appraisal_id)
    step = _active_step(caller, appraisal, ReviewStage.LEAD)
    comment = _clean_text(comment, "comment", required=False)

    validated(appraisal, answers_for(appraisal, ReviewStage.LEAD), ReviewStage.LEAD, MODE_SUBMIT)

    next_step = next(
        (
            item
            for item in appraisal.steps_for_attempt()
            if item.sequence_no == step.sequence_no + 1
        ),
        None,
    )
    if next_step is None:
        raise errors.Internal("the lead step has no manager step after it")

    now = utc_now()
    step.status = StepStatus.COMPLETED
    step.completed_at = now
    next_step.status = StepStatus.ACTIVE
    next_step.activated_at = now
    appraisal.current_reviewer_user_id = next_step.reviewer_user_id
    appraisal.current_review_step_id = next_step.id

    if comment:
        _add_comment(appraisal, step, CommentKind.COMMENT, comment)
    record_event(
        appraisal.cycle_id,
        caller.user_id,
        "lead_review_completed",
        appraisal=appraisal,
        from_status=AppraisalStatus.AWAITING_REVIEW,
        to_status=AppraisalStatus.AWAITING_REVIEW,
        details={"next_stage": next_step.review_stage, "next_reviewer": next_step.reviewer_user_id},
    )
    notifications.enqueue(
        NotificationType.REVIEW_REQUESTED,
        next_step.reviewer_user_id,
        appraisal,
        {"stage": next_step.review_stage},
    )
    db.session.commit()

    return appraisal


def record_one_to_one(caller, appraisal_id, held_at=None, notes=None):
    """Record that the Manager held the one-to-one; required before approval by default."""
    require_user(caller)
    appraisal = get_appraisal(appraisal_id)
    step = _active_step(caller, appraisal, ReviewStage.MANAGER)
    notes = _clean_text(notes, "notes", required=False)

    now = utc_now()
    if held_at is None:
        held_at = now
    else:
        try:
            held_at = datetime.fromisoformat(held_at.replace("Z", "+00:00"))
        except (AttributeError, ValueError) as exc:
            raise errors.InvalidArgument("held_at must be an ISO 8601 date-time") from exc
        if held_at.tzinfo is None:
            held_at = held_at.replace(tzinfo=timezone.utc)
        if held_at > now + ONE_TO_ONE_FUTURE_TOLERANCE:
            raise errors.InvalidArgument("held_at cannot be in the future")

    appraisal.one_to_one_held_at = held_at
    if notes:
        _add_comment(appraisal, step, CommentKind.ONE_TO_ONE_NOTE, notes)
    record_event(
        appraisal.cycle_id,
        caller.user_id,
        "one_to_one_recorded",
        appraisal=appraisal,
        details={"held_at": held_at.isoformat()},
    )
    db.session.commit()

    return appraisal


def request_changes(caller, appraisal_id, feedback):
    """Send the appraisal back to the employee with feedback; the Lead or the Manager may do this.

    The current round is kept as history and a new round starts from a copy of the employee's
    answers. Whether a returned appraisal is always resubmitted is still open (OQ-6).
    """
    require_user(caller)
    appraisal = get_appraisal(appraisal_id)
    step = _active_step(caller, appraisal)
    feedback = _clean_text(feedback, "feedback", required=True)

    now = utc_now()
    step.status = StepStatus.RETURNED
    step.completed_at = now
    for other in appraisal.steps_for_attempt():
        if other.status == StepStatus.PENDING:
            other.status = StepStatus.CANCELLED

    _add_comment(appraisal, step, CommentKind.FEEDBACK, feedback)

    previous_attempt = appraisal.review_attempt
    employee_answers = answers_for(appraisal, ReviewStage.EMPLOYEE, previous_attempt)
    appraisal.review_attempt = previous_attempt + 1
    for key, value in employee_answers.items():
        db.session.add(
            Answer(
                appraisal_id=appraisal.id,
                review_attempt=appraisal.review_attempt,
                review_stage=ReviewStage.EMPLOYEE,
                field_key=key,
                value=value,
                updated_by=caller.user_id,
            )
        )

    appraisal.status = AppraisalStatus.CHANGES_REQUESTED
    appraisal.current_reviewer_user_id = None
    appraisal.current_review_step_id = None
    appraisal.one_to_one_held_at = None

    record_event(
        appraisal.cycle_id,
        caller.user_id,
        "changes_requested",
        appraisal=appraisal,
        from_status=AppraisalStatus.AWAITING_REVIEW,
        to_status=AppraisalStatus.CHANGES_REQUESTED,
        details={"stage": step.review_stage, "attempt": previous_attempt},
    )
    notifications.enqueue(
        NotificationType.CHANGES_REQUESTED,
        appraisal.user_id,
        appraisal,
        {"stage": step.review_stage},
    )
    db.session.commit()

    return appraisal


def approve(caller, appraisal_id, comment=None):
    """Final approval by the Manager (FR-13); the appraisal becomes read-only (FR-14).

    Raises
    ------
    common.errors.FailedPrecondition
        If the one-to-one is required and not recorded, or it is not the Manager's turn.
    """
    require_user(caller)
    appraisal = get_appraisal(appraisal_id)
    step = _active_step(caller, appraisal, ReviewStage.MANAGER)
    comment = _clean_text(comment, "comment", required=False)

    if any(item.sequence_no > step.sequence_no for item in appraisal.steps_for_attempt()):
        raise errors.FailedPrecondition("later review steps are still outstanding")
    if appraisal.cycle.require_one_to_one and appraisal.one_to_one_held_at is None:
        raise errors.FailedPrecondition("record the one-to-one before approving")

    validated(
        appraisal, answers_for(appraisal, ReviewStage.MANAGER), ReviewStage.MANAGER, MODE_SUBMIT
    )

    now = utc_now()
    step.status = StepStatus.COMPLETED
    step.completed_at = now
    appraisal.status = AppraisalStatus.APPROVED
    appraisal.approved_at = now
    appraisal.current_reviewer_user_id = None
    appraisal.current_review_step_id = None

    if comment:
        _add_comment(appraisal, step, CommentKind.COMMENT, comment)
    record_event(
        appraisal.cycle_id,
        caller.user_id,
        "approved",
        appraisal=appraisal,
        from_status=AppraisalStatus.AWAITING_REVIEW,
        to_status=AppraisalStatus.APPROVED,
    )
    notifications.enqueue(NotificationType.APPRAISAL_APPROVED, appraisal.user_id, appraisal)
    db.session.commit()

    return appraisal


def reassign_reviewer(caller, appraisal_id, stage, reviewer_user_id):
    """Let an admin replace the Lead or Manager of an appraisal, or fill a missing one (§10.2).

    A step already completed in the current round cannot be reassigned; send the appraisal back
    to restart the round instead.
    """
    require_feature(caller, CYCLE_ADMIN_FEATURE, "write")
    appraisal = get_appraisal(appraisal_id)

    if appraisal.cycle.status == CycleStatus.CLOSED:
        raise errors.FailedPrecondition("the cycle is closed")
    if appraisal.status == AppraisalStatus.APPROVED:
        raise errors.FailedPrecondition("approved appraisals cannot be reassigned")
    if stage not in ReviewStage.REVIEWERS:
        raise errors.InvalidArgument("stage must be lead or manager")
    if stage == ReviewStage.LEAD and appraisal.role_type_code == "lead":
        raise errors.InvalidArgument("a lead's appraisal has no lead step")
    if reviewer_user_id == appraisal.user_id:
        raise errors.InvalidArgument("nobody can review their own appraisal")

    users = directory_client().users(ids=[reviewer_user_id])
    reviewer = users[0] if users else None
    if reviewer is None or not reviewer.is_active:
        raise errors.InvalidArgument(f"user {reviewer_user_id} is not an active user")
    if reviewer.role_type_code != stage:
        raise errors.InvalidArgument(f"the {stage} reviewer must have the {stage} role type")

    step = next(
        (item for item in appraisal.steps_for_attempt() if item.review_stage == stage), None
    )
    if step is not None and step.status == StepStatus.COMPLETED:
        raise errors.FailedPrecondition(
            f"the {stage} step is already complete in this round; send the appraisal back to "
            "restart it"
        )

    previous_user_id = (
        appraisal.lead_user_id if stage == ReviewStage.LEAD else appraisal.manager_user_id
    )
    if stage == ReviewStage.LEAD:
        appraisal.lead_user_id = reviewer.id
        appraisal.lead_name = reviewer.full_name
    else:
        appraisal.manager_user_id = reviewer.id
        appraisal.manager_name = reviewer.full_name

    if step is not None and step.status in (StepStatus.ACTIVE, StepStatus.PENDING):
        step.reviewer_user_id = reviewer.id
        step.reviewer_name = reviewer.full_name
        if step.status == StepStatus.ACTIVE:
            appraisal.current_reviewer_user_id = reviewer.id
            notifications.enqueue(
                NotificationType.REVIEW_REQUESTED, reviewer.id, appraisal, {"stage": stage}
            )

    previous_status = appraisal.status
    chain_complete = appraisal.manager_user_id is not None and (
        appraisal.role_type_code == "lead" or appraisal.lead_user_id is not None
    )
    if appraisal.status == AppraisalStatus.NEEDS_REVIEWER and chain_complete:
        appraisal.status = AppraisalStatus.NOT_STARTED
        appraisal.needs_reviewer_reason = None

    record_event(
        appraisal.cycle_id,
        caller.user_id,
        "reviewer_reassigned",
        appraisal=appraisal,
        from_status=previous_status,
        to_status=appraisal.status,
        details={"stage": stage, "from_user_id": previous_user_id, "to_user_id": reviewer.id},
    )
    db.session.commit()

    return appraisal
