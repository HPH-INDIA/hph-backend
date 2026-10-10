"""Daily chart targets adjusted using manual time deductions only."""
from decimal import Decimal, ROUND_HALF_UP


def adjusted_cpd(record, daily_target):
    """Return no target when a user's stage has no configured target."""
    if daily_target is None:
        return None

    def hours(value):
        return Decimal(str(value or 0))

    meetings = record.meetings
    if meetings is not None:
        meeting_hours = sum((hours(item["hours"]) for item in meetings
                             if item.get("type") != "Huddle"), Decimal("0"))
    else:
        meeting_hours = (Decimal("0") if record.meeting_type == "Huddle"
                         else hours(record.meeting_engagement_hours))
    available = max(Decimal("0"), Decimal("8")
                    - hours(record.tech_issues_downtime_hours)
                    - hours(record.no_inventory_idle_time_hours)
                    - hours(record.leave_hours) - meeting_hours)
    return (available * hours(daily_target) / Decimal("8")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP)


def combined_daily_target(pvp_count, foundation_count, main_target, foundation_target):
    """Eight-hour capacity for the actual chart mix, using standard time.

    Each chart earns 8 / its program target standard hours. The equivalent
    raw-chart target is total charts / earned day fractions. No allocation
    forecast or per-program hours are inferred. Without a weekly Foundation
    rate, the existing main-stage rate covers all charts.
    """
    if foundation_target is None or foundation_target == main_target:
        return main_target
    pvp, foundation = Decimal(pvp_count or 0), Decimal(foundation_count or 0)
    if not pvp and not foundation:
        return None  # different rates, but no observed mix
    if not foundation:
        return main_target
    if not pvp:
        return foundation_target
    if main_target is None or main_target <= 0 or foundation_target <= 0:
        return None  # cannot assign standard time to a zero/missing quota
    earned_days = pvp / Decimal(main_target) + foundation / Decimal(foundation_target)
    return ((pvp + foundation) / earned_days).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def foundation_targets_for(records):
    """Resolve all date-specific Foundation rates in a constant query count."""
    from app.cohorts.models import FoundationTargetRule, UserStageEvidence
    from app.cohorts.progression import foundation_progress
    from app.users.models import User
    from app.extensions import db

    if not records:
        return {}
    ids = {record.user_id for record in records}
    evidence = {row.user_id: row for row in UserStageEvidence.query.filter(UserStageEvidence.user_id.in_(ids))}
    departures = dict(db.session.query(User.id, User.last_working_day).filter(User.id.in_(ids)))
    rules = FoundationTargetRule.query.all()
    targets = {}
    for record in records:
        ev = evidence.get(record.user_id)
        code = foundation_progress(ev.first_pvp_completed, ev.first_foundation_completed,
                                   record.record_date, departures.get(record.user_id))["current_stage"] if ev else None
        targets[record.id] = next((rule.daily_target for rule in rules
                                   if rule.stage_code == code and rule.effective_from <= record.record_date
                                   and (rule.effective_to is None or record.record_date < rule.effective_to)), None)
    return targets


def snapshot_manual_productivity(records):
    """Save targets after writes and stage recomputation, never during reads.

    Resolve targets in one query for a whole import chunk. Inclusive stage
    end dates and exclusive rule end dates match cohort target resolution.
    """
    if not records:
        return
    from app.cohorts.models import StageTargetRule, UserStagePeriod
    from app.extensions import db
    from app.manual_daily_records.models import ManualDailyRecord

    # Newly created records need IDs; newly computed stage periods must be
    # visible to the target query before taking the snapshot.
    db.session.flush()
    targets = dict(db.session.query(
        ManualDailyRecord.id, StageTargetRule.daily_target,
    ).outerjoin(UserStagePeriod, db.and_(
        UserStagePeriod.user_id == ManualDailyRecord.user_id,
        UserStagePeriod.start_date <= ManualDailyRecord.record_date,
        db.or_(UserStagePeriod.end_date.is_(None),
               UserStagePeriod.end_date >= ManualDailyRecord.record_date),
    )).outerjoin(StageTargetRule, db.and_(
        StageTargetRule.stage_code == UserStagePeriod.stage_code,
        StageTargetRule.effective_from <= ManualDailyRecord.record_date,
        db.or_(StageTargetRule.effective_to.is_(None),
               StageTargetRule.effective_to > ManualDailyRecord.record_date),
    )).filter(ManualDailyRecord.id.in_([record.id for record in records])).all())
    foundation_targets = foundation_targets_for(records)
    for record in records:
        record.pvp_daily_target = targets[record.id]
        record.foundation_daily_target = foundation_targets[record.id]
        record.daily_target = combined_daily_target(record.pvp_count, record.foundation_count,
                                                    record.pvp_daily_target, record.foundation_daily_target)
        record.adjusted_cpd = adjusted_cpd(record, record.daily_target)
