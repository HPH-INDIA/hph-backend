"""Manager stage restarts, audit history, and atomic productivity updates."""
from collections import defaultdict

from flask_smorest import abort
from sqlalchemy import text
from sqlalchemy.orm import joinedload

from app.cohorts.models import CohortMembership, UserStageChange, UserStageEvidence
from app.cohorts.progression import MAIN_STAGES, FOUNDATION_STAGES, business_today
from app.extensions import db
from app.manual_daily_records.models import ManualDailyRecord
from app.manual_daily_records.productivity import snapshot_manual_productivity
from app.users.hierarchy import manager_team_user_ids
from app.users.models import User


def stage_changes_by_user(user_ids, program=None):
    query = UserStageChange.query.options(joinedload(UserStageChange.created_by)).filter(UserStageChange.user_id.in_(user_ids))
    if program:
        query = query.filter_by(program=program)
    result = defaultdict(list)
    for row in query.order_by(UserStageChange.effective_from, UserStageChange.id):
        result[row.user_id].append(row)
    return result


def recalculate_user_records(user_id, effective_from, last_working_day=None):
    query = ManualDailyRecord.query.filter(ManualDailyRecord.user_id == user_id,
                                          ManualDailyRecord.record_date >= effective_from)
    if last_working_day:
        query = query.filter(ManualDailyRecord.record_date <= last_working_day)
    records = query.order_by(ManualDailyRecord.id).all()
    for offset in range(0, len(records), 500):
        snapshot_manual_productivity(records[offset:offset + 500])
    return len(records)


def change_user_stage(actor, user_id, program, stage_code, effective_from, reason):
    from app.cohorts.stage_refresh import rebuild_main_periods

    if actor.role.role_type.code != "manager" or actor.project is None or actor.project.name != "CODING":
        abort(403, message="Only a CODING manager may change user stages.")
    if program not in ("main", "foundation"):
        abort(400, message="Unknown stage program.")
    if stage_code is not None and stage_code not in (MAIN_STAGES if program == "main" else FOUNDATION_STAGES):
        abort(400, message="Choose a stage from the selected program.")
    if not reason or not reason.strip():
        abort(400, message="Enter a reason for the stage change.")
    try:
        # Match import/target writer lock order, then block manual submissions
        # until periods and every affected saved target are committed together.
        db.session.execute(text("SELECT pg_advisory_xact_lock(740821, 1)"))
        db.session.execute(text("SELECT pg_advisory_xact_lock(740821, 2)"))
        user = db.session.query(User).filter_by(id=user_id).with_for_update().first()
        if user is None or user_id not in manager_team_user_ids(actor.id) or user.project_id != actor.project_id:
            abort(403, message="You may change stages only for users in your CODING team.")
        if user.role.role_type.code not in ("lead", "employee"):
            abort(403, message="Only CODING leads and employees have user stages.")
        membership = CohortMembership.query.filter_by(user_id=user_id).first()
        joined = user.join_date or (membership.joined_on if membership else None)
        if joined is None:
            abort(400, message="Record the user's joining date before changing stages.")
        if effective_from < joined or effective_from > business_today():
            abort(400, message="Effective date must be between joining date and today (IST).")
        if user.last_working_day and effective_from > user.last_working_day:
            abort(400, message="Effective date cannot be after the user's last working day.")
        evidence = db.session.get(UserStageEvidence, user_id)
        if program == "foundation" and stage_code is not None:
            if evidence is None or evidence.first_foundation_completed is None:
                abort(400, message="Foundation stages start after the first completed Foundation chart.")
            if effective_from < evidence.first_foundation_completed:
                abort(400, message="Effective date cannot precede the first Foundation completion.")
        prior = UserStageChange.query.filter_by(user_id=user_id, program=program).order_by(
            UserStageChange.effective_from.desc(), UserStageChange.id.desc()).first()
        if prior and effective_from < prior.effective_from:
            abort(409, message=f"Effective date cannot precede the latest {program} stage change ({prior.effective_from}).")
        db.session.execute(text("LOCK TABLE manual_daily_records IN SHARE ROW EXCLUSIVE MODE"))
        change = UserStageChange(user_id=user_id, program=program, stage_code=stage_code,
                                 effective_from=effective_from, reason=reason.strip(), created_by_id=actor.id,
                                 recalculated_records=0)
        db.session.add(change)
        db.session.flush()
        if program == "main":
            rebuild_main_periods(user, evidence=evidence, membership=membership)
        change.recalculated_records = recalculate_user_records(user_id, effective_from, user.last_working_day)
        db.session.commit()
        return change
    except Exception:
        db.session.rollback()
        raise
