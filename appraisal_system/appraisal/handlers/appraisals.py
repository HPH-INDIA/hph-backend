"""Handlers for an appraisal and the caller's own appraisals."""

from common.connexion_app import current_caller
from services import appraisals as appraisals_service, serializers


def list_my_appraisals(cycle_id=None):
    caller = current_caller()
    appraisals = appraisals_service.list_my_appraisals(caller, cycle_id=cycle_id)

    return [serializers.appraisal_summary_to_dict(appraisal) for appraisal in appraisals], 200


def get_appraisal(appraisal_id):
    caller = current_caller()
    appraisal = appraisals_service.get_visible_appraisal(caller, appraisal_id)

    return serializers.appraisal_detail_to_dict(caller, appraisal), 200


def save_answers(appraisal_id, body):
    caller = current_caller()
    appraisal = appraisals_service.save_answers(caller, appraisal_id, body.get("answers"))

    return serializers.appraisal_detail_to_dict(caller, appraisal), 200


def submit(appraisal_id):
    caller = current_caller()
    appraisal = appraisals_service.submit(caller, appraisal_id)

    return serializers.appraisal_detail_to_dict(caller, appraisal), 200
