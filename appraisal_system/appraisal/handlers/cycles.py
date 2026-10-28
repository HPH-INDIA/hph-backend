"""Handlers for /cycles; operationIds in appraisal.yaml point here."""

from common.connexion_app import current_caller
from services import cycles as cycles_service, launch as launch_service, serializers
from services.permissions import is_cycle_admin


def list_cycles(status=None):
    caller = current_caller()
    cycles = cycles_service.list_cycles(caller, status=status)

    return [serializers.cycle_to_dict(cycle) for cycle in cycles], 200


def create_cycle(body):
    caller = current_caller()
    cycle = cycles_service.create_cycle(caller, body)

    return serializers.cycle_to_dict(cycle, include_launch_summary=True), 201


def get_cycle(cycle_id):
    caller = current_caller()
    cycle = cycles_service.get_visible_cycle(caller, cycle_id)

    return serializers.cycle_to_dict(cycle, include_launch_summary=is_cycle_admin(caller)), 200


def update_cycle(cycle_id, body):
    caller = current_caller()
    cycle = cycles_service.update_cycle(caller, cycle_id, body)

    return serializers.cycle_to_dict(cycle, include_launch_summary=True), 200


def launch_cycle(cycle_id):
    caller = current_caller()
    summary = launch_service.launch_cycle(caller, cycle_id)
    cycle = cycles_service.get_cycle(cycle_id)

    return {"cycle": serializers.cycle_to_dict(cycle, include_launch_summary=True), **summary}, 200


def close_cycle(cycle_id):
    caller = current_caller()
    cycle = cycles_service.close_cycle(caller, cycle_id)

    return serializers.cycle_to_dict(cycle, include_launch_summary=True), 200


def get_progress(cycle_id):
    return cycles_service.cycle_progress(current_caller(), cycle_id), 200
