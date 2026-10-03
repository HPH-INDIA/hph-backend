"""Handlers for reviewing appraisals: inbox, Lead hand-off, one-to-one, send back, approve."""

from common.connexion_app import current_caller
from services import reviews as reviews_service, serializers


def _body(body):
    return body or {}


def inbox(status="active", cycle_id=None):
    caller = current_caller()
    items = reviews_service.inbox(caller, status=status, cycle_id=cycle_id)

    return [serializers.inbox_item_to_dict(step, appraisal) for step, appraisal in items], 200


def complete_lead_review(appraisal_id, body=None):
    caller = current_caller()
    appraisal = reviews_service.complete_lead_review(
        caller, appraisal_id, comment=_body(body).get("comment")
    )

    return serializers.appraisal_detail_to_dict(caller, appraisal), 200


def record_one_to_one(appraisal_id, body=None):
    caller = current_caller()
    values = _body(body)
    appraisal = reviews_service.record_one_to_one(
        caller, appraisal_id, held_at=values.get("held_at"), notes=values.get("notes")
    )

    return serializers.appraisal_detail_to_dict(caller, appraisal), 200


def request_changes(appraisal_id, body):
    caller = current_caller()
    appraisal = reviews_service.request_changes(caller, appraisal_id, body.get("feedback"))

    return serializers.appraisal_detail_to_dict(caller, appraisal), 200


def approve(appraisal_id, body=None):
    caller = current_caller()
    appraisal = reviews_service.approve(caller, appraisal_id, comment=_body(body).get("comment"))

    return serializers.appraisal_detail_to_dict(caller, appraisal), 200


def reassign_reviewer(appraisal_id, body):
    caller = current_caller()
    appraisal = reviews_service.reassign_reviewer(
        caller, appraisal_id, body.get("stage"), body.get("reviewer_user_id")
    )

    return serializers.appraisal_detail_to_dict(caller, appraisal), 200
