"""The stage-computation and target-resolution algorithm - a direct
formalization of Daily_Refresh/build/etl.py's STAGE_ORDER/STAGE_TARGET cascade
and per-coder `master` table, corrected per the dev spec: M1's start comes
from observed activity (first_activity_date) rather than hand-typed text, and
stage targets are resolved from a date-versioned rule instead of a flat
constant.
"""
from datetime import date, timedelta

from flask_smorest import abort
from sqlalchemy.exc import IntegrityError

from app.cohorts.models import (
    Cohort,
    CohortMembership,
    CohortJoinReview,
    CoderStagePeriod,
    KaironCompletion,
    ManualProductionFact,
    Stage,
    StageException,
    StageTargetRule,
    UserStagePeriod,
)
from app.extensions import db
from app.kairon.models import KaironChartRecord, KaironUploadBatch
from app.manual_daily_records.models import ManualDailyRecord
from app.roles.models import Role, RoleType
from app.users.models import Project, User
from app.users.hierarchy import manager_lead_team_user_ids, manager_team_user_ids

TRAINING_STAGE = "Training"
# M4 is now a permanent part of the path for any coder ramping today - the
# "cohorts that predate M4" branch in the dev spec's pseudocode only ever
# applied to historical cohorts, whose periods are loaded directly from the
# legacy ramp workbook by the one-time import rather than recomputed here.
STAGE_PATH_AFTER_TRAINING = ("M1", "M2", "M3", "M4", "Steady State")

# The spec proposes "7-14 days either side" and leaves it unfixed. A module
# constant keeps it a one-line change until it's promoted to real config.
AMBIGUITY_BUFFER_DAYS = 10
TARGET_STAGES = ("M1", "M2", "M3", "M4", "Steady State")


def first_activity_date(coder):
    """The first date this coder shows real chart activity: a manual
    production count greater than zero, falling back to their first
    completed Kairon chart if the manual file has no record for them. This
    is `FirstGridActivity` in the legacy pipeline, promoted here from a
    cross-check to the authoritative M1 start date.
    """
    manual_date = (
        db.session.query(db.func.min(ManualProductionFact.activity_date))
        .filter(
            ManualProductionFact.coder_id == coder.id,
            ManualProductionFact.production_count > 0,
        )
        .scalar()
    )
    if manual_date is not None:
        return manual_date

    return (
        db.session.query(db.func.min(KaironCompletion.completed_date))
        .filter(KaironCompletion.coder_id == coder.id)
        .scalar()
    )


def _extra_days_by_stage(coder):
    totals = {}
    for exception in StageException.query.filter_by(coder_id=coder.id).all():
        totals[exception.stage_code] = totals.get(exception.stage_code, 0) + exception.extra_days
    return totals


def _apply_exceptions(coder, periods_by_stage):
    """Extends the named stage's end date by extra_days, then shifts every
    later stage's start and end by the same amount - this coder only. Reads
    stage_exceptions fresh each call rather than trusting a stored counter,
    since coder_stage_periods is itself rebuilt from scratch on every call.
    """
    extra_by_stage = _extra_days_by_stage(coder)
    if not extra_by_stage:
        return

    cumulative_shift = 0
    for stage_code in (TRAINING_STAGE, *STAGE_PATH_AFTER_TRAINING):
        period = periods_by_stage.get(stage_code)
        if period is None:
            continue  # coder hasn't reached this stage yet - nothing to shift

        if cumulative_shift:
            period["start_date"] += timedelta(days=cumulative_shift)
            if period["end_date"] is not None:
                period["end_date"] += timedelta(days=cumulative_shift)
        period["shifted_by_exception_days"] = cumulative_shift

        this_stage_extra = extra_by_stage.get(stage_code, 0)
        if this_stage_extra:
            if period["end_date"] is not None:
                period["end_date"] += timedelta(days=this_stage_extra)
            cumulative_shift += this_stage_extra


def load_stages_by_code():
    """The seeded `stages` table keyed by code. Callers recomputing many
    coders in a loop (e.g. the Phase 2 import) should load this once and
    pass it to compute_stage_periods rather than re-querying per coder.
    """
    return {stage.code: stage for stage in Stage.query.all()}


def compute_stage_periods(coder, stages_by_code=None):
    """Recomputes this coder's full Training->Steady State timeline and
    replaces their coder_stage_periods rows. This is a read-through cache,
    not hand-edited data - call it again (new activity, a new exception, a
    cohort change) rather than patching existing rows.
    """
    if stages_by_code is None:
        stages_by_code = load_stages_by_code()

    periods = []

    m1_start = first_activity_date(coder)
    periods.append(
        {
            "stage_code": TRAINING_STAGE,
            "start_date": coder.join_date,
            "end_date": (m1_start - timedelta(days=1)) if m1_start else None,
            "source": "observed_first_activity",
            "shifted_by_exception_days": 0,
        }
    )

    if m1_start is not None:
        cursor = m1_start
        for stage_code in STAGE_PATH_AFTER_TRAINING:
            stage = stages_by_code[stage_code]
            end = None if stage.duration_days is None else cursor + timedelta(days=stage.duration_days - 1)
            periods.append(
                {
                    "stage_code": stage_code,
                    "start_date": cursor,
                    "end_date": end,
                    "source": "calendar_offset",
                    "shifted_by_exception_days": 0,
                }
            )
            if end is None:
                break
            cursor = end + timedelta(days=1)

    periods_by_stage = {p["stage_code"]: p for p in periods}
    _apply_exceptions(coder, periods_by_stage)

    CoderStagePeriod.query.filter_by(coder_id=coder.id).delete()
    rows = [CoderStagePeriod(coder_id=coder.id, **period) for period in periods]
    db.session.add_all(rows)
    db.session.commit()
    return rows


def current_stage_period(coder, on_date):
    return CoderStagePeriod.query.filter(
        CoderStagePeriod.coder_id == coder.id,
        CoderStagePeriod.start_date <= on_date,
        db.or_(CoderStagePeriod.end_date.is_(None), CoderStagePeriod.end_date >= on_date),
    ).first()


def target_for_period(period, on_date):
    if period is None:
        return None
    rule = StageTargetRule.query.filter(
        StageTargetRule.stage_code == period.stage_code,
        StageTargetRule.effective_from <= on_date,
        db.or_(StageTargetRule.effective_to.is_(None), StageTargetRule.effective_to > on_date),
    ).first()
    return rule.daily_target if rule else None


def get_daily_target(coder, on_date):
    """A coder's target on any day, looked up live - never cached as a flat
    constant, so a target-rule edit is reflected immediately for every date
    it covers, past or future.
    """
    return target_for_period(current_stage_period(coder, on_date), on_date)


def overlapping_rule_exists(stage_code, effective_from, effective_to, exclude_id=None):
    """Mirrors the non-overlap check the Postgres exclusion constraint
    enforces at the DB layer, so the API can return a clean 409 instead of
    surfacing a raw IntegrityError on the common case - the constraint
    itself stays as the race-condition backstop, not the primary path.
    Ranges are half-open ([from, to)); two overlap iff each one's start is
    before the other's end (an open/null end never blocks anything before it).
    """
    query = StageTargetRule.query.filter(StageTargetRule.stage_code == stage_code)
    if exclude_id is not None:
        query = query.filter(StageTargetRule.id != exclude_id)
    if effective_to is not None:
        query = query.filter(StageTargetRule.effective_from < effective_to)
    query = query.filter(
        db.or_(StageTargetRule.effective_to.is_(None), StageTargetRule.effective_to > effective_from)
    )
    return db.session.query(query.exists()).scalar()


def assign_cohort(coder):
    """Assigns `coder` to the current open cohort, unless their join date
    falls near another cohort's window too - in which case nothing is
    guessed: a cohort_join_review row is created and the coder stays
    unassigned until a human resolves it.
    """
    current = Cohort.query.filter(Cohort.window_end.is_(None)).order_by(Cohort.sequence_no.desc()).first()
    if current is None:
        return  # no cohort has ever been opened yet - nothing to assign into

    nearby_ids = [
        cohort.id
        for cohort in Cohort.query.filter(Cohort.id != current.id).all()
        if abs((coder.join_date - cohort.window_start).days) <= AMBIGUITY_BUFFER_DAYS
    ]

    if not nearby_ids:
        coder.cohort_id = current.id
        db.session.commit()
        return

    coder.cohort_id = None
    db.session.add(
        CohortJoinReview(
            coder_id=coder.id,
            candidate_cohort_ids=[current.id, *nearby_ids],
            status="pending",
        )
    )
    db.session.commit()


def eligible_coding_users_for_manager(manager):
    """Return this manager's active CODING leads and employees."""
    if manager.project is None or manager.project.name != "CODING":
        return []
    team_ids = manager_team_user_ids(manager.id)
    if not team_ids:
        return []
    return (
        User.query.join(Role)
        .join(RoleType)
        .join(Project, User.project_id == Project.id)
        .filter(
            User.id.in_(team_ids),
            User.is_active.is_(True),
            Project.name == "CODING",
            RoleType.code.in_(("lead", "employee")),
        )
        .order_by(User.first_name, User.last_name)
        .all()
    )


def get_team_coder_overview(manager, page, page_size, cohort_id=None, lead_id=None, stage_code=None):
    from app.cohorts.stage_refresh import team_stage_overview
    return team_stage_overview(manager, page, page_size, cohort_id, lead_id, stage_code)


def first_user_activity_date(user_id):
    return (
        db.session.query(db.func.min(KaironChartRecord.completed_date))
        .join(KaironUploadBatch)
        .filter(
            KaironChartRecord.user_id == user_id,
            KaironChartRecord.status == "Completed",
            KaironChartRecord.completed_date.isnot(None),
            KaironUploadBatch.superseded_at.is_(None),
            KaironUploadBatch.status == "completed",
        )
        .scalar()
    )


def compute_user_stage_periods(membership, stages_by_code=None):
    from app.cohorts.stage_refresh import rebuild_main_periods
    user = membership if isinstance(membership, User) else membership.user
    return rebuild_main_periods(user)


def create_user_cohort(manager, label, window_start, member_ids, window_end=None):
    """Create a future cohort and assign members from the manager's CODING team."""
    eligible = {user.id: user for user in eligible_coding_users_for_manager(manager)}
    requested = set(member_ids)
    invalid = sorted(requested - set(eligible))
    if invalid:
        abort(400, message="Every member must be an active CODING lead or employee in your team.", data={"userIds": invalid})

    already_assigned = [
        membership.user_id
        for membership in CohortMembership.query.filter(CohortMembership.user_id.in_(requested)).all()
    ] if requested else []
    if already_assigned:
        abort(409, message="One or more selected users already belong to a cohort.", data={"userIds": already_assigned})

    prior_open = Cohort.query.filter(Cohort.window_end.is_(None)).order_by(Cohort.sequence_no.desc()).first()
    if prior_open is not None and prior_open.window_start < window_start:
        prior_open.window_end = window_start - timedelta(days=1)

    sequence_no = (db.session.query(db.func.max(Cohort.sequence_no)).scalar() or 0) + 1
    cohort = Cohort(
        sequence_no=sequence_no,
        label=label or f"Cohort {sequence_no}",
        window_start=window_start,
        window_end=window_end,
    )
    db.session.add(cohort)
    db.session.flush()

    stages_by_code = load_stages_by_code()
    for user_id in member_ids:
        membership = CohortMembership(
            cohort_id=cohort.id,
            user_id=user_id,
            joined_on=eligible[user_id].join_date or window_start,
            assigned_by_id=manager.id,
        )
        db.session.add(membership)
        db.session.flush()
        compute_user_stage_periods(membership, stages_by_code=stages_by_code)

    db.session.commit()
    return cohort


def change_stage_target(actor, stage_code, effective_from=None, daily_target=None, reason=None, apply_from=None):
    from app.cohorts.target_changes import change_target
    return change_target(actor, stage_code, daily_target, effective_from=effective_from,
                         apply_from=apply_from, reason=reason)
