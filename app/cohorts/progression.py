"""Calendar rules shared by stage refresh and the Teams read model."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

MAIN_STAGES = ("M1", "M2", "M3", "M4", "Steady State")
FOUNDATION_STAGES = ("W1", "W2", "W3", "W4", "Steady State")


def business_today():
    return datetime.now(ZoneInfo("Asia/Kolkata")).date()


def _period(code, start, end, source):
    return dict(stage_code=code, start_date=start, end_date=end, source=source,
                shifted_by_exception_days=0)


def main_periods(joined_on, first_completed, last_working_day=None):
    """Inclusive dates; never fabricate a training end or an inverted period."""
    if first_completed and joined_on and first_completed < joined_on:
        first_completed = None  # conflicting evidence needs a corrected DOJ/chart
    periods = []
    if joined_on and (first_completed is None or joined_on < first_completed):
        periods.append(_period("Training", joined_on,
                               first_completed - timedelta(days=1) if first_completed else None,
                               "observed_first_activity"))
    if first_completed:
        for index, code in enumerate(MAIN_STAGES):
            start = first_completed + timedelta(days=30 * index)
            end = start + timedelta(days=29) if index < 4 else None
            periods.append(_period(code, start, end,
                                   "observed_first_activity" if index == 0 else "calendar_offset"))
    return cap_periods(periods, last_working_day)


def cap_periods(periods, last_working_day):
    if last_working_day is None:
        return periods
    return [dict(p, end_date=min(p["end_date"] or date.max, last_working_day))
            for p in periods if p["start_date"] <= last_working_day]


def period_on(periods, day):
    return next((p for p in periods if p["start_date"] <= day
                 and (p["end_date"] is None or day <= p["end_date"])), None)


def foundation_progress(first_pvp, first_foundation, day, last_working_day=None):
    """Completed-date order is authoritative; date-only ties are unresolved."""
    result = dict(first_pvp_completed=first_pvp, first_foundation_completed=first_foundation,
                  current_stage=None, daily_target=None, periods=[])
    if first_foundation is None:
        return dict(result, eligibility="awaiting_foundation")
    if first_pvp is None or first_foundation < first_pvp:
        return dict(result, eligibility="foundation_first")
    if first_foundation == first_pvp:
        return dict(result, eligibility="same_day_unknown")
    periods = []
    for index, code in enumerate(FOUNDATION_STAGES):
        start = first_foundation + timedelta(days=7 * index)
        periods.append(_period(code, start, start + timedelta(days=6) if index < 4 else None,
                               "observed_first_activity" if index == 0 else "calendar_offset"))
    periods = cap_periods(periods, last_working_day)
    current = period_on(periods, day)
    return dict(result, eligibility="eligible", periods=periods,
                current_stage=current["stage_code"] if current else None)
