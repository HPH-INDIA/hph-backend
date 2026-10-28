"""Map appraisal models to the response shapes in ``appraisal.yaml``."""

from models import AppraisalStatus, CycleStatus, ReviewStage, StepStatus
from services.appraisals import (
    ADMIN,
    OWNER,
    answers_for,
    relations,
    visible_comments,
    visible_stages,
)
from services.permissions import is_cycle_admin


def _timestamp(value):
    return value.isoformat() if value else None


def cycle_to_dict(cycle, include_launch_summary=False):
    payload = {
        "id": cycle.id,
        "name": cycle.name,
        "scope": cycle.scope,
        "project_id": cycle.project_id,
        "frequency": cycle.frequency,
        "start_date": cycle.start_date.isoformat(),
        "end_date": cycle.end_date.isoformat(),
        "submission_due": cycle.submission_due.isoformat(),
        "review_due": cycle.review_due.isoformat(),
        "require_one_to_one": cycle.require_one_to_one,
        "status": cycle.status,
        "created_at": _timestamp(cycle.created_at),
        "launched_at": _timestamp(cycle.launched_at),
        "closed_at": _timestamp(cycle.closed_at),
    }
    if include_launch_summary:
        payload["launch_summary"] = cycle.launch_summary

    return payload


def _cycle_brief(cycle):
    return {
        "id": cycle.id,
        "name": cycle.name,
        "status": cycle.status,
        "submission_due": cycle.submission_due.isoformat(),
        "review_due": cycle.review_due.isoformat(),
    }


def step_to_dict(step):
    return {
        "id": step.id,
        "attempt": step.review_attempt,
        "sequence_no": step.sequence_no,
        "stage": step.review_stage,
        "reviewer_user_id": step.reviewer_user_id,
        "reviewer_name": step.reviewer_name,
        "status": step.status,
        "activated_at": _timestamp(step.activated_at),
        "completed_at": _timestamp(step.completed_at),
    }


def appraisal_summary_to_dict(appraisal):
    step = appraisal.current_step
    return {
        "id": appraisal.id,
        "cycle": _cycle_brief(appraisal.cycle),
        "user_id": appraisal.user_id,
        "employee_name": appraisal.employee_name,
        "employee_emp_id": appraisal.employee_emp_id,
        "project_id": appraisal.project_id,
        "role_type_code": appraisal.role_type_code,
        "status": appraisal.status,
        "current_stage": step.review_stage if step else None,
        "current_reviewer_user_id": step.reviewer_user_id if step else None,
        "current_reviewer_name": step.reviewer_name if step else None,
        "submitted_at": _timestamp(appraisal.submitted_at),
        "approved_at": _timestamp(appraisal.approved_at),
    }


def available_actions(caller, appraisal):
    """What the caller can do right now, so the frontend shows the right buttons."""
    actions = []
    if appraisal.cycle.status != CycleStatus.ACTIVE:
        return actions

    found = relations(caller, appraisal)
    if (
        OWNER in found
        and caller.can("appraisal_self", "write")
        and appraisal.status in AppraisalStatus.EDITABLE_BY_EMPLOYEE
    ):
        actions += ["save_answers", "submit"]

    step = appraisal.current_step
    if (
        appraisal.status == AppraisalStatus.AWAITING_REVIEW
        and step is not None
        and step.reviewer_user_id == caller.user_id
        and caller.can("appraisal_review", "write")
    ):
        actions += ["save_answers", "request_changes"]
        if step.review_stage == ReviewStage.LEAD:
            actions.append("complete_lead_review")
        else:
            actions.append("record_one_to_one")
            if not appraisal.cycle.require_one_to_one or appraisal.one_to_one_held_at:
                actions.append("approve")

    if (
        ADMIN in found
        and is_cycle_admin(caller, "write")
        and appraisal.status != AppraisalStatus.APPROVED
    ):
        actions.append("reassign_reviewer")

    return actions


def _comment_to_dict(comment):
    return {
        "id": comment.id,
        "attempt": comment.review_attempt,
        "stage": comment.review_stage,
        "kind": comment.kind,
        "author_user_id": comment.author_user_id,
        "author_name": comment.author_name,
        "body": comment.body,
        "created_at": _timestamp(comment.created_at),
    }


def _answers_by_stage(appraisal, stages, attempt):
    return {
        stage: answers_for(appraisal, stage, attempt)
        for stage in ReviewStage.ALL
        if stage in stages
    }


def appraisal_detail_to_dict(caller, appraisal):
    """Full appraisal for the caller, hiding answers and notes they may not see yet."""
    stages = visible_stages(caller, appraisal)
    payload = appraisal_summary_to_dict(appraisal)

    payload.update(
        {
            "form": {
                "form_id": appraisal.form_id,
                "version_id": appraisal.form_version_id,
                "name": appraisal.form_name,
                "definition": appraisal.form_definition,
            },
            "review_attempt": appraisal.review_attempt,
            "lead": (
                {"user_id": appraisal.lead_user_id, "name": appraisal.lead_name}
                if appraisal.lead_user_id
                else None
            ),
            "manager": (
                {"user_id": appraisal.manager_user_id, "name": appraisal.manager_name}
                if appraisal.manager_user_id
                else None
            ),
            "needs_reviewer_reason": appraisal.needs_reviewer_reason,
            "one_to_one_held_at": _timestamp(appraisal.one_to_one_held_at),
            "review_chain": [step_to_dict(step) for step in appraisal.steps_for_attempt()],
            "answers": _answers_by_stage(appraisal, stages, appraisal.review_attempt),
            "comments": [
                _comment_to_dict(comment) for comment in visible_comments(caller, appraisal)
            ],
            "previous_attempts": [
                {
                    "attempt": attempt,
                    "answers": _answers_by_stage(appraisal, stages, attempt),
                    "review_chain": [
                        step_to_dict(step) for step in appraisal.steps_for_attempt(attempt)
                    ],
                }
                for attempt in range(1, appraisal.review_attempt)
            ],
            "available_actions": available_actions(caller, appraisal),
        }
    )

    return payload


def inbox_item_to_dict(step, appraisal):
    return {
        "appraisal_id": appraisal.id,
        "cycle": _cycle_brief(appraisal.cycle),
        "user_id": appraisal.user_id,
        "employee_name": appraisal.employee_name,
        "employee_emp_id": appraisal.employee_emp_id,
        "project_id": appraisal.project_id,
        "role_type_code": appraisal.role_type_code,
        "stage": step.review_stage,
        "step_status": step.status,
        "appraisal_status": appraisal.status,
        "attempt": step.review_attempt,
        "submitted_at": _timestamp(appraisal.submitted_at),
        "activated_at": _timestamp(step.activated_at),
        "completed_at": _timestamp(step.completed_at),
        "one_to_one_held_at": _timestamp(appraisal.one_to_one_held_at),
        "is_actionable": step.status == StepStatus.ACTIVE,
    }
