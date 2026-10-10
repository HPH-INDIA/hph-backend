"""Audited target replacement and explicit, atomic saved-CPD recalculation."""
from datetime import date

from flask_smorest import abort
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.cohorts.models import (
    FoundationTargetRule, StageTargetChange, StageTargetRule, UserStageEvidence, UserStagePeriod,
)
from app.cohorts.progression import FOUNDATION_STAGES, MAIN_STAGES, business_today, foundation_progress
from app.extensions import db
from app.manual_daily_records.models import ManualDailyRecord
from app.manual_daily_records.productivity import snapshot_manual_productivity
from app.users.models import User


def _rule_snapshot(rule):
    return dict(id=rule.id, stage_code=rule.stage_code, daily_target=rule.daily_target,
                effective_from=rule.effective_from.isoformat(),
                effective_to=rule.effective_to.isoformat() if rule.effective_to else None,
                created_by_id=rule.created_by_id, reason=rule.reason,
                created_at=rule.created_at.isoformat() if rule.created_at else None)


def _affected_records(stage_code, effective_from, foundation):
    from app.cohorts.stage_refresh import coding_users_query
    users = coding_users_query().with_entities(User.id)
    query = ManualDailyRecord.query.join(User, ManualDailyRecord.user_id == User.id).filter(
        ManualDailyRecord.user_id.in_(users),
        ManualDailyRecord.record_date >= effective_from,
        db.or_(User.last_working_day.is_(None), ManualDailyRecord.record_date <= User.last_working_day),
    )
    if not foundation:
        return query.join(UserStagePeriod, db.and_(
            UserStagePeriod.user_id == ManualDailyRecord.user_id,
            UserStagePeriod.stage_code == stage_code,
            UserStagePeriod.start_date <= ManualDailyRecord.record_date,
            db.or_(UserStagePeriod.end_date.is_(None), UserStagePeriod.end_date >= ManualDailyRecord.record_date),
        )).order_by(ManualDailyRecord.id).all()
    candidates = query.join(UserStageEvidence, UserStageEvidence.user_id == ManualDailyRecord.user_id).filter(
        UserStageEvidence.first_pvp_completed < UserStageEvidence.first_foundation_completed,
        ManualDailyRecord.record_date >= UserStageEvidence.first_foundation_completed,
    ).add_entity(UserStageEvidence).order_by(ManualDailyRecord.id).all()
    return [record for record, evidence in candidates if foundation_progress(
        evidence.first_pvp_completed, evidence.first_foundation_completed, record.record_date,
    )["current_stage"] == stage_code]


def change_target(actor, stage_code, daily_target, *, foundation=False,
                  apply_from=None, effective_from=None, reason=None):
    if actor.project is None or actor.project.name != "CODING":
        abort(403, message="Only a CODING manager may configure stage targets.")
    if stage_code not in (FOUNDATION_STAGES if foundation else MAIN_STAGES):
        abort(400, message="Unknown target stage.")
    if not isinstance(daily_target, int) or isinstance(daily_target, bool) or not 0 <= daily_target <= 2147483647:
        abort(400, message="Daily target must be a non-negative whole number.")
    if apply_from is not None and (apply_from not in ("today", "program_start") or effective_from is not None):
        abort(400, message="Choose either today or program start.")
    today = business_today()
    if apply_from is None and (effective_from is None or effective_from < today):
        abort(400, message="Historical changes require the program-start choice.")
    model = FoundationTargetRule if foundation else StageTargetRule
    try:
        # Keep stage evidence stable and serialize target writers. Block manual
        # writes before inspecting rows: an in-flight submission must complete
        # before the scan, or resolve targets after this transaction commits.
        db.session.execute(text("SELECT pg_advisory_xact_lock(740821, 1)"))
        db.session.execute(text("SELECT pg_advisory_xact_lock(740821, 2)"))
        if apply_from is not None:
            db.session.execute(text("LOCK TABLE manual_daily_records IN SHARE ROW EXCLUSIVE MODE"))
        rules = model.query.filter_by(stage_code=stage_code).order_by(model.effective_from).with_for_update().all()
        if not rules:
            abort(409, message="No existing target rules were found for this stage.")
        if apply_from == "program_start":
            # The original rule covers the whole program, including older
            # records imported later. Do not anchor history to today's roster.
            effective_from = min(date(1900, 1, 1), rules[0].effective_from)
        elif apply_from == "today":
            effective_from = today
        current = next((r for r in rules if r.effective_from <= effective_from
                        and (r.effective_to is None or effective_from < r.effective_to)), None)
        if apply_from is None:
            # Compatibility for clients that still schedule explicit dates.
            if current is None or current.effective_from == effective_from:
                abort(409, message="A rule already starts on that date, or no existing rule covers it.")
            if any(r.effective_from > effective_from for r in rules):
                abort(409, message="A future target change is already scheduled for this stage.")
        audit = StageTargetChange(
            program="foundation" if foundation else "main", stage_code=stage_code,
            apply_from=apply_from or "scheduled", effective_from=effective_from,
            daily_target=daily_target, created_by_id=actor.id, reason=reason,
            previous_rules=[_rule_snapshot(r) for r in rules], recalculated_records=0,
        )
        db.session.add(audit)
        for old in rules:
            if old.effective_from >= effective_from:
                db.session.delete(old)
            elif old.effective_to is None or old.effective_to > effective_from:
                old.effective_to = effective_from
        db.session.flush()  # exclusion constraints require the old ranges to close first
        rule = model(stage_code=stage_code, effective_from=effective_from, daily_target=daily_target,
                     created_by_id=actor.id, reason=reason)
        db.session.add(rule)
        if apply_from is not None:
            records = _affected_records(stage_code, effective_from, foundation)
            for offset in range(0, len(records), 500):
                snapshot_manual_productivity(records[offset:offset + 500])
            audit.recalculated_records = len(records)
        rule.recalculated_records = audit.recalculated_records
        rule.apply_from = audit.apply_from
        db.session.commit()
        return rule
    except IntegrityError:
        db.session.rollback()
        abort(409, message="This target change conflicts with another update. Refresh and try again.")
    except Exception:
        db.session.rollback()
        raise
