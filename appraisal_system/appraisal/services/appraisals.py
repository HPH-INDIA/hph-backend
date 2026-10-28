"""Filling and submitting an appraisal (FR-10, FR-14) and who may see what."""

import common.errors as errors
from clients import forms_client
from models import (
    Answer,
    Appraisal,
    AppraisalStatus,
    CommentKind,
    Cycle,
    CycleStatus,
    NotificationType,
    ReviewStage,
    ReviewStep,
    StepStatus,
    db,
    utc_now,
)
from services import notifications
from services.audit import record_event
from services.permissions import (
    REVIEW_FEATURE,
    SELF_FEATURE,
    is_cycle_admin,
    require_feature,
    require_user,
)


MODE_DRAFT = "draft"
MODE_SUBMIT = "submit"

OWNER = "owner"
LEAD = "lead"
MANAGER = "manager"
ADMIN = "admin"


def get_appraisal(appraisal_id):
    appraisal = db.session.get(Appraisal, appraisal_id)
    if appraisal is None:
        raise errors.NotFound(f"appraisal {appraisal_id} not found")

    return appraisal


def relations(caller, appraisal):
    """Return how *caller* relates to *appraisal*: owner, lead, manager and/or admin."""
    found = set()
    if caller.is_service:
        return found

    if caller.user_id == appraisal.user_id and caller.can(SELF_FEATURE):
        found.add(OWNER)

    if caller.can(REVIEW_FEATURE):
        lead_ids = {appraisal.lead_user_id}
        manager_ids = {appraisal.manager_user_id}
        for step in appraisal.steps:
            (lead_ids if step.review_stage == ReviewStage.LEAD else manager_ids).add(
                step.reviewer_user_id
            )
        if caller.user_id in lead_ids:
            found.add(LEAD)
        if caller.user_id in manager_ids:
            found.add(MANAGER)

    if is_cycle_admin(caller):
        found.add(ADMIN)

    return found


def get_visible_appraisal(caller, appraisal_id):
    """Return the appraisal if the caller owns, reviews or administers it (NFR-2).

    Raises
    ------
    common.errors.PermissionDenied
        For anyone else (AC-6).
    """
    require_user(caller)
    appraisal = get_appraisal(appraisal_id)
    if not relations(caller, appraisal):
        raise errors.PermissionDenied("you cannot view this appraisal")

    return appraisal


def visible_stages(caller, appraisal):
    """Which stages' answers the caller may read.

    Managers and admins see everything. A Lead sees the employee's and their own answers. The
    appraised person sees their own answers, and the reviewers' once the appraisal is approved.
    Everyone in the chain sees the reviewers' answers after approval.
    """
    found = relations(caller, appraisal)
    if found & {MANAGER, ADMIN} or appraisal.status == AppraisalStatus.APPROVED:
        return set(ReviewStage.ALL)

    stages = set()
    if OWNER in found:
        stages.add(ReviewStage.EMPLOYEE)
    if LEAD in found:
        stages.update({ReviewStage.EMPLOYEE, ReviewStage.LEAD})

    return stages


def visible_comments(caller, appraisal):
    """Comments the caller may read (OQ-8).

    Feedback that sends the appraisal back is shown to everyone: the employee has to act on it.
    One-to-one notes stay between the Manager and the employee (and admins), so a Lead never sees
    them, even after approval. Other comments follow the visibility of their stage's answers.
    """
    found = relations(caller, appraisal)
    stages = visible_stages(caller, appraisal)

    visible = []
    for comment in appraisal.comments:
        if comment.kind == CommentKind.FEEDBACK:
            visible.append(comment)
        elif comment.kind == CommentKind.ONE_TO_ONE_NOTE:
            approved_owner = OWNER in found and appraisal.status == AppraisalStatus.APPROVED
            if found & {MANAGER, ADMIN} or approved_owner:
                visible.append(comment)
        elif comment.review_stage in stages:
            visible.append(comment)

    return visible


def list_my_appraisals(caller, cycle_id=None):
    require_feature(caller, SELF_FEATURE, "read")

    query = (
        db.session.query(Appraisal)
        .join(Cycle)
        .filter(Appraisal.user_id == caller.user_id, Cycle.status != CycleStatus.DRAFT)
    )
    if cycle_id is not None:
        query = query.filter(Appraisal.cycle_id == cycle_id)

    return query.order_by(Cycle.start_date.desc(), Appraisal.id.desc()).all()


def answers_for(appraisal, stage, attempt=None):
    """Return ``{field_key: value}`` for one stage of one attempt (current attempt by default)."""
    attempt = appraisal.review_attempt if attempt is None else attempt
    rows = (
        db.session.query(Answer)
        .filter(
            Answer.appraisal_id == appraisal.id,
            Answer.review_attempt == attempt,
            Answer.review_stage == stage,
        )
        .all()
    )

    return {row.field_key: row.value for row in rows}


def require_open_cycle(appraisal):
    if appraisal.cycle.status != CycleStatus.ACTIVE:
        raise errors.FailedPrecondition(
            f"the cycle is {appraisal.cycle.status}; nothing can change"
        )


def validated(appraisal, answers, stage, mode):
    """Check answers with the Form Builder against the version this appraisal pinned."""
    normalised, problems = forms_client().validate(appraisal.form_version_id, answers, stage, mode)
    if problems:
        message = (
            "some answers are invalid" if mode == MODE_DRAFT else "complete the required fields"
        )
        raise errors.InvalidArgument(message, data={"errors": problems})

    return normalised


def store_answers(appraisal, stage, normalised, user_id):
    """Upsert one stage's answers in the current attempt; empty values delete the row."""
    attempt = appraisal.review_attempt
    for key, value in normalised.items():
        existing = db.session.get(Answer, (appraisal.id, attempt, stage, key))
        if value is None or value == []:
            if existing is not None:
                db.session.delete(existing)
            continue

        if existing is None:
            db.session.add(
                Answer(
                    appraisal_id=appraisal.id,
                    review_attempt=attempt,
                    review_stage=stage,
                    field_key=key,
                    value=value,
                    updated_by=user_id,
                )
            )
        else:
            existing.value = value
            existing.updated_by = user_id


def _writer_stage(caller, appraisal):
    """Return the stage the caller is allowed to write right now."""
    if caller.user_id == appraisal.user_id:
        require_feature(caller, SELF_FEATURE, "write")
        if appraisal.status == AppraisalStatus.NEEDS_REVIEWER:
            raise errors.FailedPrecondition("waiting for an admin to assign a reviewer")
        if appraisal.status not in AppraisalStatus.EDITABLE_BY_EMPLOYEE:
            raise errors.FailedPrecondition(
                f"this appraisal is {appraisal.status} and cannot be edited"
            )
        return ReviewStage.EMPLOYEE

    step = appraisal.current_step
    if (
        appraisal.status == AppraisalStatus.AWAITING_REVIEW
        and step is not None
        and step.reviewer_user_id == caller.user_id
    ):
        require_feature(caller, REVIEW_FEATURE, "write")
        return step.review_stage

    if relations(caller, appraisal):
        raise errors.FailedPrecondition("it is not your turn to edit this appraisal")

    raise errors.PermissionDenied("you cannot edit this appraisal")


def save_answers(caller, appraisal_id, answers):
    """Save a draft of the caller's own stage: the employee's form, or the active reviewer's
    section. Values are checked for type and limits; required fields only on submit.
    """
    require_user(caller)
    appraisal = get_appraisal(appraisal_id)
    require_open_cycle(appraisal)

    stage = _writer_stage(caller, appraisal)
    normalised = validated(appraisal, answers, stage, MODE_DRAFT)
    store_answers(appraisal, stage, normalised, caller.user_id)

    if stage == ReviewStage.EMPLOYEE and appraisal.status == AppraisalStatus.NOT_STARTED:
        appraisal.status = AppraisalStatus.DRAFT
        record_event(
            appraisal.cycle_id,
            caller.user_id,
            "draft_started",
            appraisal=appraisal,
            from_status=AppraisalStatus.NOT_STARTED,
            to_status=AppraisalStatus.DRAFT,
        )

    db.session.commit()

    return appraisal


def review_chain(appraisal):
    """Return the ordered ``(stage, user_id, name)`` reviewers for this appraisal."""
    manager = (ReviewStage.MANAGER, appraisal.manager_user_id, appraisal.manager_name)
    if appraisal.role_type_code == "lead":
        return [manager]

    return [(ReviewStage.LEAD, appraisal.lead_user_id, appraisal.lead_name), manager]


def submit(caller, appraisal_id):
    """Submit the employee's answers; the first reviewer's step becomes active (FR-10).

    Raises
    ------
    common.errors.InvalidArgument
        If required fields are missing; the list is in ``data.errors``.
    common.errors.FailedPrecondition
        If the appraisal is waiting for a reviewer, already submitted, or the cycle is not active.
    """
    require_user(caller)
    appraisal = get_appraisal(appraisal_id)
    if caller.user_id != appraisal.user_id:
        raise errors.PermissionDenied("only the person being appraised can submit it")

    require_open_cycle(appraisal)
    _writer_stage(caller, appraisal)

    validated(
        appraisal, answers_for(appraisal, ReviewStage.EMPLOYEE), ReviewStage.EMPLOYEE, MODE_SUBMIT
    )

    chain = review_chain(appraisal)
    if any(user_id is None for _, user_id, _ in chain):
        raise errors.FailedPrecondition("the review chain is incomplete; an admin must assign it")
    if appraisal.steps_for_attempt():
        raise errors.FailedPrecondition("this round was already submitted")

    now = utc_now()
    steps = [
        ReviewStep(
            appraisal=appraisal,
            review_attempt=appraisal.review_attempt,
            sequence_no=index + 1,
            review_stage=stage,
            reviewer_user_id=user_id,
            reviewer_name=name,
            status=StepStatus.ACTIVE if index == 0 else StepStatus.PENDING,
            activated_at=now if index == 0 else None,
        )
        for index, (stage, user_id, name) in enumerate(chain)
    ]
    db.session.add_all(steps)
    db.session.flush()

    first = steps[0]
    previous_status = appraisal.status
    appraisal.status = AppraisalStatus.AWAITING_REVIEW
    appraisal.submitted_at = now
    appraisal.current_reviewer_user_id = first.reviewer_user_id
    appraisal.current_review_step_id = first.id

    record_event(
        appraisal.cycle_id,
        caller.user_id,
        "submitted",
        appraisal=appraisal,
        from_status=previous_status,
        to_status=AppraisalStatus.AWAITING_REVIEW,
        details={"attempt": appraisal.review_attempt, "next_stage": first.review_stage},
    )
    notifications.enqueue(
        NotificationType.REVIEW_REQUESTED,
        first.reviewer_user_id,
        appraisal,
        {"stage": first.review_stage},
    )
    db.session.commit()

    return appraisal
