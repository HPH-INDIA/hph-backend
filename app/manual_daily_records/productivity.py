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
    for record in records:
        record.daily_target = targets[record.id]
        record.adjusted_cpd = adjusted_cpd(record, record.daily_target)
