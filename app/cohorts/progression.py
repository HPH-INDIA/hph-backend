"""Calendar progression, with dated manager restarts applied to each program."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TRAINING_DAYS = 14
MAIN_STAGES = ("M1", "M2", "M3", "M4", "Steady State")
FOUNDATION_STAGES = ("W1", "W2", "W3", "W4", "Steady State")


def business_today():
    return datetime.now(ZoneInfo("Asia/Kolkata")).date()


def _period(code, start, end, source):
    return dict(stage_code=code, start_date=start, end_date=end, source=source,
                shifted_by_exception_days=0)


def _path(start, codes, days, source="calendar_offset"):
    return [_period(code, start + timedelta(days=days * index),
                    start + timedelta(days=days * (index + 1) - 1) if code != "Steady State" else None,
                    source) for index, code in enumerate(codes)]


def apply_stage_changes(baseline, changes, codes, days):
    """Preserve history before each restart; a reset resumes the original path.

    Changes are immutable audit events. Multiple restarts can revisit a stage;
    same-day edits use the last event without creating overlapping periods.
    """
    periods = list(baseline)
    for change in sorted(changes or [], key=lambda row: (row.effective_from, row.id)):
        start = change.effective_from
        prior = [dict(p, end_date=min(p["end_date"] or date.max, start - timedelta(days=1)))
                 for p in periods if p["start_date"] < start]
        if change.stage_code is None:
            tail = [dict(p, start_date=max(p["start_date"], start)) for p in baseline
                    if p["end_date"] is None or p["end_date"] >= start]
        else:
            tail = _path(start, codes[codes.index(change.stage_code):], days, "manual_override")
        periods = prior + tail
    return periods


def main_periods(joined_on, first_completed=None, last_working_day=None, *, changes=None):
    """Training is joining day through day 13; M1 starts on joining day + 14.

    first_completed is retained for callers using the earlier signature. It
    is evidence only and never starts, delays, or shortens the main ramp.
    """
    if joined_on is None:
        return []
    m1_start = joined_on + timedelta(days=TRAINING_DAYS)
    baseline = [_period("Training", joined_on, m1_start - timedelta(days=1), "calendar_offset")]
    baseline += _path(m1_start, MAIN_STAGES, 30)
    valid_changes = [row for row in changes or [] if row.effective_from >= joined_on]
    return cap_periods(apply_stage_changes(baseline, valid_changes, MAIN_STAGES, 30), last_working_day)


def cap_periods(periods, last_working_day):
    if last_working_day is None:
        return periods
    return [dict(p, end_date=min(p["end_date"] or date.max, last_working_day))
            for p in periods if p["start_date"] <= last_working_day]


def period_on(periods, day):
    return next((p for p in periods if p["start_date"] <= day
                 and (p["end_date"] is None or day <= p["end_date"])), None)


def foundation_progress(first_pvp, first_foundation, day, last_working_day=None, *, changes=None):
    """Every user starts W1 on their first Foundation completed_date."""
    baseline = _path(first_foundation, FOUNDATION_STAGES, 7, "observed_first_activity") if first_foundation else []
    periods = cap_periods(apply_stage_changes(baseline, changes, FOUNDATION_STAGES, 7), last_working_day)
    current = period_on(periods, day)
    return dict(first_pvp_completed=first_pvp, first_foundation_completed=first_foundation,
                eligibility="eligible" if periods else "awaiting_foundation",
                current_stage=current["stage_code"] if current else None,
                daily_target=None, periods=periods)
