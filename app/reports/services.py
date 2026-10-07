"""Aggregation/read layer for the personal Reports view and the Coding
project dashboard - no new source-of-truth tables, everything here reads
or updates rows already owned by app.kairon and app.manual_daily_records.
"""
import calendar
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from flask_smorest import abort
from sqlalchemy import case, func

from app.cohorts.models import Cohort, CohortMembership, StageTargetRule, UserStagePeriod
from app.extensions import db
from app.kairon.models import KaironChartRecord, KaironUploadBatch
from app.kairon.production import unique_completed_production
from app.login_hours.models import LoginHourRecord
from app.manual_daily_records.models import ManualDailyRecord
from app.manual_daily_records.services import approve_record, manual_review_user_ids, reject_record
from app.reports.models import OfficeHoliday
from app.roles.models import Role, RoleType
from app.users.models import Project, User
from app.users.hierarchy import lead_employee_user_ids, manager_team_user_ids


FULL_WORKDAY_MINUTES = 8 * 60
EFFICIENCY_CAP_PERCENT = Decimal("120.0")


def _hours_to_minutes(value):
    return int((Decimal(value or 0) * 60).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _percent(actual, target):
    if target <= 0:
        return None
    raw = Decimal(actual) * 100 / target
    return min(raw, EFFICIENCY_CAP_PERCENT).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _cpd(actual, effective_minutes):
    if effective_minutes is None or effective_minutes <= 0:
        return None
    return (Decimal(actual) * FULL_WORKDAY_MINUTES / effective_minutes).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )


def _manual_count(record, program=None):
    if record is None:
        return 0
    if program == "PVP":
        return record.pvp_count
    if program == "FOUNDATION":
        return record.foundation_count
    return record.production_count


def _month_window(month_value=None):
    today = date.today()
    if month_value:
        try:
            year, month = (int(part) for part in month_value.split("-"))
            first = date(year, month, 1)
        except (AttributeError, TypeError, ValueError):
            abort(400, message="month must be a valid 'YYYY-MM' value.")
    else:
        first = today.replace(day=1)
    return first, date(first.year, first.month, calendar.monthrange(first.year, first.month)[1])


def get_monthly_goal(user, month_value=None):
    """Preserve the existing employee/lead monthly-goal contract."""
    month_start, month_end = _month_window(month_value)
    is_lead = user.role.role_type.code == "lead"
    members = [user, *user.direct_reports] if is_lead else [user]
    return get_period_goal(members, month_start, month_end, "team" if is_lead else "self")


def get_period_goal(members, from_date, to_date, scope="team", program=None):
    """Batch calendar targets for explicitly scoped users and a reporting period.

    Exclude weekends, office holidays, and full leave; apply saved CPD
    reductions without recomputing them. Keep date-effective stage targets
    and employment end dates. Per-user totals allow separate QA/coder rollups
    without repeating the source queries for each person.
    """
    # Do not carry people into a month that begins after their employment.
    members = [
        member
        for member in members
        if member.last_working_day is None or member.last_working_day >= from_date
    ]
    member_ids = [member.id for member in members]
    holidays = {
        row.holiday_date
        for row in OfficeHoliday.query.filter(
            OfficeHoliday.holiday_date >= from_date,
            OfficeHoliday.holiday_date <= to_date,
        ).all()
    }
    weekday_holidays = {day for day in holidays if day.weekday() < 5}

    periods = UserStagePeriod.query.filter(
        UserStagePeriod.user_id.in_(member_ids),
        UserStagePeriod.start_date <= to_date,
        db.or_(UserStagePeriod.end_date.is_(None), UserStagePeriod.end_date >= from_date),
    ).all() if member_ids else []
    rules = StageTargetRule.query.filter(
        StageTargetRule.effective_from <= to_date,
        db.or_(StageTargetRule.effective_to.is_(None), StageTargetRule.effective_to > from_date),
    ).all()
    manual_rows = ManualDailyRecord.query.filter(
        ManualDailyRecord.user_id.in_(member_ids),
        ManualDailyRecord.record_date >= from_date,
        ManualDailyRecord.record_date <= to_date,
        ManualDailyRecord.status != "rejected",
    ).all() if member_ids else []
    manual_by_day = {(row.user_id, row.record_date): row for row in manual_rows}
    full_leave_days = {
        (row.user_id, row.record_date) for row in manual_rows if row.leave_hours >= Decimal("8")
    }

    periods_by_user = {}
    for period in periods:
        periods_by_user.setdefault(period.user_id, []).append(period)
    rules_by_stage = {}
    for rule in rules:
        rules_by_stage.setdefault(rule.stage_code, []).append(rule)

    target_charts = 0
    eligible_days = 0
    leave_days_excluded = 0
    targets_by_user = {member.id: 0 for member in members}
    eligible_by_user = {member.id: 0 for member in members}
    leave_by_user = {member.id: 0 for member in members}
    adjusted_targets_by_user = {member.id: Decimal("0.00") for member in members}
    for member in members:
        employment_end = min(to_date, member.last_working_day) if member.last_working_day else to_date
        work_date = from_date
        while work_date <= employment_end:
            if work_date.weekday() < 5 and work_date not in holidays:
                period = next(
                    (
                        item
                        for item in periods_by_user.get(member.id, [])
                        if item.start_date <= work_date
                        and (item.end_date is None or item.end_date >= work_date)
                    ),
                    None,
                )
                rule = next(
                    (
                        item
                        for item in rules_by_stage.get(period.stage_code if period else None, [])
                        if item.effective_from <= work_date
                        and (item.effective_to is None or item.effective_to > work_date)
                    ),
                    None,
                )
                if rule is not None:
                    if (member.id, work_date) in full_leave_days:
                        leave_days_excluded += 1
                        leave_by_user[member.id] += 1
                    else:
                        eligible_days += 1
                        eligible_by_user[member.id] += 1
                        target_charts += rule.daily_target
                        targets_by_user[member.id] += rule.daily_target
                        adjusted_daily_target = Decimal(rule.daily_target)
                        record = manual_by_day.get((member.id, work_date))
                        if (record is not None and record.daily_target is not None
                                and record.adjusted_cpd is not None):
                            # The saved stage target is the basis for its saved
                            # reduction. Clamp to zero if a later rule edit has
                            # lowered the calendar-day target below that reduction.
                            reduction = max(Decimal("0"), record.daily_target - record.adjusted_cpd)
                            adjusted_daily_target = max(Decimal("0"), adjusted_daily_target - reduction)
                        adjusted_targets_by_user[member.id] += adjusted_daily_target
            work_date += timedelta(days=1)

    completed_through = min(to_date, date.today())
    completed_charts = 0
    completed_by_user = {}
    manual_by_user = {}
    if member_ids and completed_through >= from_date:
        completed_by_user = dict(
            db.session.query(KaironChartRecord.user_id, func.count(KaironChartRecord.id))
            .join(KaironUploadBatch, KaironChartRecord.batch_id == KaironUploadBatch.id)
            .join(User, KaironChartRecord.user_id == User.id)
            .filter(
                KaironUploadBatch.superseded_at.is_(None),
                KaironChartRecord.status == "Completed",
                func.upper(KaironChartRecord.program) == program if program else True,
                unique_completed_production(),
                KaironChartRecord.user_id.in_(member_ids),
                KaironChartRecord.completed_date >= from_date,
                KaironChartRecord.completed_date <= completed_through,
                db.or_(
                    User.last_working_day.is_(None),
                    KaironChartRecord.completed_date <= User.last_working_day,
                ),
            )
            .group_by(KaironChartRecord.user_id)
            .all()
        )
        completed_charts = sum(completed_by_user.values())
        manual_by_user = dict(
            db.session.query(ManualDailyRecord.user_id, func.sum(ManualDailyRecord.pvp_count if program == "PVP" else
                ManualDailyRecord.foundation_count if program == "FOUNDATION" else ManualDailyRecord.production_count))
            .join(User, ManualDailyRecord.user_id == User.id)
            .filter(
                ManualDailyRecord.user_id.in_(member_ids),
                ManualDailyRecord.status != "rejected",
                ManualDailyRecord.record_date >= from_date,
                ManualDailyRecord.record_date <= completed_through,
                db.or_(User.last_working_day.is_(None), ManualDailyRecord.record_date <= User.last_working_day),
            )
            .group_by(ManualDailyRecord.user_id)
            .all()
        )

    calendar_working_days = sum(
        1
        for offset in range((to_date - from_date).days + 1)
        if (from_date + timedelta(days=offset)).weekday() < 5
        and (from_date + timedelta(days=offset)) not in holidays
    )
    return {
        "month": from_date.strftime("%Y-%m"),
        "from_date": from_date,
        "to_date": to_date,
        "scope": scope,
        "user_count": len(members),
        "completed_charts": completed_charts,
        "manual_charts": sum(manual_by_user.values()),
        "adjusted_target_charts": sum(adjusted_targets_by_user.values(), Decimal("0.00")),
        "adjusted_difference": sum(adjusted_targets_by_user.values(), Decimal("0.00"))
            - sum(manual_by_user.values()),
        "users": [
            {
                "user_id": member.id,
                "name": f"{member.first_name} {member.last_name}".strip(),
                "manual_charts": manual_by_user.get(member.id, 0),
                "completed_charts": completed_by_user.get(member.id, 0),
                "target_charts": targets_by_user[member.id],
                "eligible_days": eligible_by_user[member.id],
                "leave_days_excluded": leave_by_user[member.id],
                "adjusted_target_charts": adjusted_targets_by_user[member.id],
                "adjusted_difference": adjusted_targets_by_user[member.id] - manual_by_user.get(member.id, 0),
                "difference": targets_by_user[member.id] - completed_by_user.get(member.id, 0),
            }
            for member in members
        ],
        "target_charts": target_charts,
        "difference": target_charts - completed_charts,
        "calendar_working_days": calendar_working_days,
        "eligible_days": eligible_days,
        "holiday_count": len(weekday_holidays),
        "leave_days_excluded": leave_days_excluded,
    }


def get_efficiency(user_ids, from_date, to_date, include_daily=False, program=None):
    """Calculate daily and period efficiency from the existing source rows.

    A full-day stage target represents eight hours. Its adjustment is based
    on recorded downtime, idle, meetings, and leave/permission -- not small
    variations in actual login time. Productive time uses actual inside time
    less downtime, idle time, and meetings. CPD follows Daily Refresh and uses
    the standard eight-hour target basis after all exclusions. Displayed
    efficiency is capped at 120%; period metrics use weighted totals.
    """
    if not user_ids:
        return {}

    employment_end_by_user = {
        user.id: user.last_working_day
        for user in User.query.filter(User.id.in_(user_ids)).all()
    }

    def within_employment(user_id, work_date):
        employment_end = employment_end_by_user.get(user_id)
        return employment_end is None or work_date <= employment_end

    login_rows = LoginHourRecord.query.filter(
        LoginHourRecord.user_id.in_(user_ids),
        LoginHourRecord.attendance_date >= from_date,
        LoginHourRecord.attendance_date <= to_date,
    ).all()
    login_rows = [
        row for row in login_rows if within_employment(row.user_id, row.attendance_date)
    ]
    manual_rows = ManualDailyRecord.query.filter(
        ManualDailyRecord.user_id.in_(user_ids),
        ManualDailyRecord.record_date >= from_date,
        ManualDailyRecord.record_date <= to_date,
    ).all()
    manual_rows = [
        row for row in manual_rows if within_employment(row.user_id, row.record_date)
    ]
    kairon_query = (
        db.session.query(
            KaironChartRecord.user_id,
            KaironChartRecord.completed_date,
            func.count(KaironChartRecord.id),
        )
        .join(KaironUploadBatch, KaironChartRecord.batch_id == KaironUploadBatch.id)
        .join(User, KaironChartRecord.user_id == User.id)
        .filter(
            KaironUploadBatch.superseded_at.is_(None),
            KaironChartRecord.status == "Completed",
            unique_completed_production(),
            KaironChartRecord.user_id.in_(user_ids),
            KaironChartRecord.completed_date >= from_date,
            KaironChartRecord.completed_date <= to_date,
            db.or_(
                User.last_working_day.is_(None),
                KaironChartRecord.completed_date <= User.last_working_day,
            ),
        )
    )
    if program:
        kairon_query = kairon_query.filter(func.upper(KaironChartRecord.program) == program)
    kairon_rows = kairon_query.group_by(
        KaironChartRecord.user_id, KaironChartRecord.completed_date
    ).all()
    periods = UserStagePeriod.query.filter(
        UserStagePeriod.user_id.in_(user_ids),
        UserStagePeriod.start_date <= to_date,
        db.or_(UserStagePeriod.end_date.is_(None), UserStagePeriod.end_date >= from_date),
    ).all()
    rules = StageTargetRule.query.filter(
        StageTargetRule.effective_from <= to_date,
        db.or_(StageTargetRule.effective_to.is_(None), StageTargetRule.effective_to > from_date),
    ).all()

    login_by_key = {(row.user_id, row.attendance_date): row for row in login_rows}
    manual_by_key = {(row.user_id, row.record_date): row for row in manual_rows}
    kairon_by_key = {(user_id, completed_date): count for user_id, completed_date, count in kairon_rows}
    periods_by_user = {}
    for period in periods:
        periods_by_user.setdefault(period.user_id, []).append(period)
    rules_by_stage = {}
    for rule in rules:
        rules_by_stage.setdefault(rule.stage_code, []).append(rule)

    results = {}
    all_keys = set(login_by_key) | set(manual_by_key) | set(kairon_by_key)
    for user_id in user_ids:
        dates = sorted((day for uid, day in all_keys if uid == user_id), reverse=True)
        daily = []
        total_manual_charts = 0
        total_kairon_charts = 0
        total_adjusted_target = Decimal("0")
        total_adjusted_cpd = None
        total_target_minutes = 0
        total_inside_minutes = 0
        login_days = 0
        total_productive_minutes = 0
        calculated_days = 0

        for work_date in dates:
            login = login_by_key.get((user_id, work_date))
            manual = manual_by_key.get((user_id, work_date))
            period = next(
                (
                    item
                    for item in periods_by_user.get(user_id, [])
                    if item.start_date <= work_date and (item.end_date is None or item.end_date >= work_date)
                ),
                None,
            )
            rule = next(
                (
                    item
                    for item in rules_by_stage.get(period.stage_code if period else None, [])
                    if item.effective_from <= work_date
                    and (item.effective_to is None or item.effective_to > work_date)
                ),
                None,
            )

            inside_minutes = login.total_inside_minutes if login else None
            downtime_minutes = _hours_to_minutes(manual.tech_issues_downtime_hours) if manual else 0
            idle_minutes = _hours_to_minutes(manual.no_inventory_idle_time_hours) if manual else 0
            leave_minutes = _hours_to_minutes(manual.leave_hours) if manual else 0
            meeting_minutes = _hours_to_minutes(manual.meeting_engagement_hours) if manual else 0
            excluded_minutes = downtime_minutes + idle_minutes + leave_minutes + meeting_minutes
            operational_deduction_minutes = downtime_minutes + idle_minutes + meeting_minutes
            productive_minutes = (
                max(inside_minutes - operational_deduction_minutes, 0)
                if inside_minutes is not None
                else None
            )
            # Daily Refresh outer-merges Manual and Kairon by coder/date. Any
            # source row therefore receives the standard eight-hour target
            # capacity, reduced by manual exclusions when they exist. Login
            # data remains necessary only for actual productive time.
            has_source_row = manual is not None or (user_id, work_date) in kairon_by_key
            target_minutes = (
                max(FULL_WORKDAY_MINUTES - excluded_minutes, 0)
                if has_source_row
                else None
            )
            daily_target = rule.daily_target if rule else None
            adjusted_target = (
                (Decimal(daily_target) * target_minutes / FULL_WORKDAY_MINUTES).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                if daily_target is not None and target_minutes is not None
                else None
            )
            manual_charts = _manual_count(manual, program)
            kairon_charts = kairon_by_key.get((user_id, work_date), 0)
            manual_efficiency_percent = (
                _percent(manual_charts, adjusted_target) if adjusted_target is not None else None
            )
            kairon_efficiency_percent = (
                _percent(kairon_charts, adjusted_target) if adjusted_target is not None else None
            )
            manual_cpd = _cpd(manual_charts, target_minutes)
            kairon_cpd = _cpd(kairon_charts, target_minutes)
            target_cpd = _cpd(adjusted_target, target_minutes) if adjusted_target is not None else None

            if has_source_row:
                total_manual_charts += manual_charts
                total_kairon_charts += kairon_charts
                total_target_minutes += target_minutes
                calculated_days += 1
            if adjusted_target is not None:
                total_adjusted_target += adjusted_target
            if manual is not None and manual.adjusted_cpd is not None:
                total_adjusted_cpd = (total_adjusted_cpd or Decimal("0")) + manual.adjusted_cpd
            if inside_minutes is not None:
                total_inside_minutes += inside_minutes
                login_days += 1
            if productive_minutes is not None:
                total_productive_minutes += productive_minutes

            if include_daily:
                daily.append(
                    {
                        "date": work_date,
                        "stage": period.stage_code if period else None,
                        "daily_target": daily_target,
                        "manual_charts": manual_charts,
                        "kairon_charts": kairon_charts,
                        "inside_minutes": inside_minutes,
                        "downtime_minutes": downtime_minutes,
                        "idle_minutes": idle_minutes,
                        "leave_minutes": leave_minutes,
                        "meeting_minutes": meeting_minutes,
                        "excluded_minutes": excluded_minutes,
                        "productive_minutes": productive_minutes,
                        "target_minutes": target_minutes,
                        "adjusted_target": adjusted_target,
                        "adjusted_cpd": manual.adjusted_cpd if manual is not None else None,
                        "manual_efficiency_percent": manual_efficiency_percent,
                        "kairon_efficiency_percent": kairon_efficiency_percent,
                        "manual_cpd": manual_cpd,
                        "kairon_cpd": kairon_cpd,
                        "target_cpd": target_cpd,
                        "manual_status": manual.status if manual else None,
                    }
                )

        results[user_id] = {
            "from_date": from_date,
            "to_date": to_date,
            "manual_charts": total_manual_charts,
            "kairon_charts": total_kairon_charts,
            "adjusted_target": total_adjusted_target.quantize(Decimal("0.01")),
            "adjusted_cpd": total_adjusted_cpd,
            "inside_minutes": total_inside_minutes,
            "login_days": login_days,
            "productive_minutes": total_productive_minutes,
            "target_minutes": total_target_minutes,
            "calculated_days": calculated_days,
            "manual_efficiency_percent": _percent(total_manual_charts, total_adjusted_target),
            "kairon_efficiency_percent": _percent(total_kairon_charts, total_adjusted_target),
            "manual_cpd": _cpd(total_manual_charts, total_target_minutes),
            "kairon_cpd": _cpd(total_kairon_charts, total_target_minutes),
            "target_cpd": _cpd(total_adjusted_target, total_target_minutes),
            "daily": daily,
        }
    return results


def resolve_dashboard_window(args):
    """Turns the query args of GET /api/dashboards/coding into a concrete
    (from_date, to_date) pair.

    Precedence: an explicit from/to range wins, then a single `date`, then
    `month` and `year` shorthands. The current month/year is clipped to
    today. With no filters, the window is the 1st of the current month
    through today (§4.2's "rolling month-to-date").
    """
    today = date.today()

    if args.get("from_date") or args.get("to_date"):
        from_date = args.get("from_date") or args["to_date"]
        to_date = args.get("to_date") or args["from_date"]
        return from_date, to_date

    if args.get("date"):
        return args["date"], args["date"]

    if args.get("month"):
        try:
            year, month = (int(part) for part in args["month"].split("-"))
            first = date(year, month, 1)
        except ValueError:
            abort(400, message="month must be a valid 'YYYY-MM' value.")
        last = date(year, month, calendar.monthrange(year, month)[1])
        return first, min(last, today)

    if args.get("year"):
        year = args["year"]
        first = date(year, 1, 1)
        last = date(year, 12, 31)
        return first, min(last, today) if year == today.year else last

    return today.replace(day=1), today


def bulk_approve_manual_records(record_ids, reviewed_by_id):
    """Approve pending records belonging to the reviewing lead's employees.

    Missing, out-of-scope, or already-decided records are skipped rather
    than failing the complete batch.
    """
    reviewable_user_ids = set(manual_review_user_ids(db.session.get(User, reviewed_by_id)))
    approved, skipped = [], []
    for record_id in record_ids:
        record = db.session.get(ManualDailyRecord, record_id)
        if record is None or record.user_id not in reviewable_user_ids:
            skipped.append({"id": record_id, "reason": "Record not found."})
        elif record.status != "pending":
            skipped.append({"id": record_id, "reason": "Record isn't pending review."})
        else:
            approve_record(record, reviewed_by_id)
            approved.append(record_id)

    db.session.commit()
    return {"approved": approved, "skipped": skipped}


def bulk_reject_manual_records(items, reviewed_by_id):
    """Same best-effort behavior as bulk_approve_manual_records(), but each
    item carries {id, reason}. A caller may repeat one reason across a batch.
    """
    reviewable_user_ids = set(manual_review_user_ids(db.session.get(User, reviewed_by_id)))
    rejected, skipped = [], []
    for item in items:
        record = db.session.get(ManualDailyRecord, item["id"])
        if record is None or record.user_id not in reviewable_user_ids:
            skipped.append({"id": item["id"], "reason": "Record not found."})
        elif record.status != "pending":
            skipped.append({"id": item["id"], "reason": "Record isn't pending review."})
        else:
            reject_record(record, reviewed_by_id, item.get("reason"))
            rejected.append(item["id"])

    db.session.commit()
    return {"rejected": rejected, "skipped": skipped}


def get_manual_team_day(viewer, record_date):
    """One selected day for every eligible coder, grouped under their lead."""
    result = get_manual_team_range(viewer, record_date, record_date)
    return {
        "date": record_date,
        "teams": [
            {
                "lead": team["lead"],
                "lead_record": next(iter(team["lead_records"]), None),
                "coders": [
                    {"user": coder["user"], "record": next(iter(coder["records"]), None)}
                    for coder in team["coders"]
                ],
            }
            for team in result["teams"]
        ],
    }


def _kairon_user_summaries(user_ids, from_date, to_date):
    """Completed program counts in the window and current holds by user."""
    summaries = {}
    def summary_for(user_id):
        return summaries.setdefault(user_id, {"pvp": 0, "foundation": 0, "on_hold": 0, "total": 0})

    for user_id, program, count in (
        db.session.query(
            KaironChartRecord.user_id,
            KaironChartRecord.program,
            func.count(KaironChartRecord.id),
        )
        .join(KaironUploadBatch, KaironChartRecord.batch_id == KaironUploadBatch.id)
        .filter(
            KaironUploadBatch.superseded_at.is_(None),
            KaironChartRecord.status == "Completed",
            unique_completed_production(),
            KaironChartRecord.completed_date >= from_date,
            KaironChartRecord.completed_date <= to_date,
            KaironChartRecord.user_id.in_(user_ids),
        )
        .group_by(KaironChartRecord.user_id, KaironChartRecord.program)
        .all()
    ):
        summary = summary_for(user_id)
        summary["total"] += count
        normalized_program = program.strip().upper()
        if normalized_program == "PVP":
            summary["pvp"] += count
        elif "FOUNDATION" in normalized_program:
            summary["foundation"] += count

    # Hold is current inventory: On Hold records have no completion date and
    # must not be mixed into the selected period's completed production.
    for user_id, count in (
        db.session.query(KaironChartRecord.user_id, func.count(KaironChartRecord.id))
        .join(KaironUploadBatch, KaironChartRecord.batch_id == KaironUploadBatch.id)
        .filter(
            KaironUploadBatch.superseded_at.is_(None),
            KaironChartRecord.status == "On Hold",
            KaironChartRecord.user_id.in_(user_ids),
        )
        .group_by(KaironChartRecord.user_id)
        .all()
    ):
        summary_for(user_id)["on_hold"] = count
    return summaries


def get_kairon_lead_team_range(lead, from_date, to_date):
    """Completed production and current holds for a lead and direct coders."""
    coder_ids = lead_employee_user_ids(lead.id)
    coders = (
        User.query.filter(User.id.in_(coder_ids))
        .order_by(User.first_name, User.last_name, User.id)
        .all()
    )
    grouped = {}
    for user_id, completed_date, count in (
        db.session.query(
            KaironChartRecord.user_id,
            KaironChartRecord.completed_date,
            func.count(KaironChartRecord.id),
        )
        .join(KaironUploadBatch, KaironChartRecord.batch_id == KaironUploadBatch.id)
        .filter(
            KaironUploadBatch.superseded_at.is_(None),
            KaironChartRecord.status == "Completed",
            unique_completed_production(),
            KaironChartRecord.completed_date >= from_date,
            KaironChartRecord.completed_date <= to_date,
            KaironChartRecord.user_id.in_([lead.id, *coder_ids]),
        )
        .group_by(KaironChartRecord.user_id, KaironChartRecord.completed_date)
        .all()
    ):
        grouped.setdefault(user_id, []).append({"date": completed_date, "count": count})
    summaries = _kairon_user_summaries([lead.id, *coder_ids], from_date, to_date)

    def summary_for(user_id):
        return summaries.get(user_id, {"pvp": 0, "foundation": 0, "on_hold": 0, "total": 0})

    def days_for(user_id):
        return sorted(grouped.get(user_id, []), key=lambda day: day["date"], reverse=True)

    def eligible(coder):
        if coder.last_working_day is not None:
            return from_date <= coder.last_working_day
        return coder.is_active or bool(grouped.get(coder.id))

    coder_entries = []
    for coder in coders:
        if eligible(coder):
            days = days_for(coder.id)
            coder_entries.append({
                "user": coder,
                "days": days,
                "count": sum(day["count"] for day in days),
                "summary": summary_for(coder.id),
            })

    return {
        "from_date": from_date,
        "to_date": to_date,
        "lead": lead,
        "lead_days": days_for(lead.id),
        "lead_summary": summary_for(lead.id),
        "coders": coder_entries,
    }


def get_kairon_manager_team_range(manager, from_date, to_date):
    """Completed Kairon production for this manager's leads and coders."""
    member_ids = manager_team_user_ids(manager.id)
    members = (
        User.query.filter(User.id.in_(member_ids))
        .order_by(User.first_name, User.last_name, User.id)
        .all()
    )
    leads = [
        member for member in members
        if member.role.role_type.code == "lead" and member.reports_to_id == manager.id
    ]
    summaries = _kairon_user_summaries(member_ids, from_date, to_date)

    def summary_for(user_id):
        return summaries.get(user_id, {"pvp": 0, "foundation": 0, "on_hold": 0, "total": 0})

    def eligible(member):
        if member.last_working_day is not None:
            return from_date <= member.last_working_day
        return member.is_active or summary_for(member.id)["total"] > 0

    coders_by_lead = {}
    for member in members:
        if member.role.role_type.code == "employee" and eligible(member):
            coders_by_lead.setdefault(member.reports_to_id, []).append({
                "user": member,
                "summary": summary_for(member.id),
            })
    teams = []
    for lead in leads:
        coders = coders_by_lead.pop(lead.id, [])
        if eligible(lead) or coders:
            teams.append({"lead": lead, "lead_summary": summary_for(lead.id), "coders": coders})
    unassigned = [coder for coders in coders_by_lead.values() for coder in coders]
    if unassigned:
        teams.append({"lead": None, "lead_summary": summary_for(None), "coders": unassigned})
    return {"from_date": from_date, "to_date": to_date, "teams": teams}


def get_manual_team_range(viewer, from_date, to_date):
    """Daily records in an inclusive range, grouped without duplicating leads."""
    if viewer.role.role_type.code == "lead":
        leads = [viewer]
        member_ids = [viewer.id, *lead_employee_user_ids(viewer.id)]
    else:
        member_ids = manager_team_user_ids(viewer.id)
        leads = (
            User.query.join(Role).join(RoleType)
            .filter(User.reports_to_id == viewer.id, RoleType.code == "lead")
            .order_by(User.first_name, User.last_name, User.id)
            .all()
        )
    members = (
        User.query.filter(User.id.in_(member_ids))
        .order_by(User.first_name, User.last_name, User.id)
        .all()
    )
    records = {}
    last_working_days = {member.id: member.last_working_day for member in members}
    for record in (
        ManualDailyRecord.query.filter(
            ManualDailyRecord.user_id.in_(member_ids),
            ManualDailyRecord.record_date >= from_date,
            ManualDailyRecord.record_date <= to_date,
        )
        .order_by(ManualDailyRecord.record_date, ManualDailyRecord.id)
        .all()
    ):
        last_working_day = last_working_days.get(record.user_id)
        if last_working_day is None or record.record_date <= last_working_day:
            records.setdefault(record.user_id, []).append(record)

    def eligible(user):
        if user.last_working_day is not None:
            return from_date <= user.last_working_day
        return user.is_active or user.id in records

    coders_by_lead = {}
    for member in members:
        if member.role.role_type.code == "employee" and eligible(member):
            coders_by_lead.setdefault(member.reports_to_id, []).append(
                {"user": member, "records": records.get(member.id, [])}
            )
    teams = []
    for lead in leads:
        coders = coders_by_lead.pop(lead.id, [])
        if eligible(lead) or coders:
            teams.append({
                "lead": lead,
                "lead_records": records.get(lead.id, []) if eligible(lead) else [],
                "coders": coders,
            })
    unassigned = [coder for coders in coders_by_lead.values() for coder in coders]
    if unassigned:
        teams.append({"lead": None, "lead_records": [], "coders": unassigned})
    return {"from_date": from_date, "to_date": to_date, "teams": teams}


def get_coding_dashboard(from_date, to_date, program=None, lead_id=None, cohort_id=None, include_daily=False):
    """One card per historically eligible user for the given window (§4.4): Kairon chart
    counts include completed charts only, scoped by each chart's completed
    date. Manual production/hours/pending totals are scoped by each record's
    own record_date. Inactive users remain visible for periods that overlap
    their employment, and their metrics stop on their last working day.
    """
    # The dashboard population is the operational coder definition, not
    # every active account in the application. Leads are production coders
    # too; their role only changes authorization/reporting hierarchy.
    users_query = (
        User.query.join(Role)
        .join(RoleType)
        .join(Project)
        .filter(
            Project.name == "CODING",
            RoleType.code.in_(("lead", "employee")),
        )
    )
    if cohort_id is not None:
        if db.session.get(Cohort, cohort_id) is None:
            abort(400, message="cohortId must reference an existing cohort.")
        users_query = users_query.filter(
            User.id.in_(
                db.session.query(CohortMembership.user_id).filter(
                    CohortMembership.cohort_id == cohort_id
                )
            )
        )
    if lead_id is not None:
        lead = (
            User.query.join(Role)
            .join(RoleType)
            .filter(User.id == lead_id, RoleType.code == "lead")
            .first()
        )
        if lead is None:
            abort(400, message="leadId must reference a lead.")
        scoped_user_ids = [lead.id, *[user.id for user in lead.direct_reports]]
        users_query = users_query.filter(User.id.in_(scoped_user_ids))

    candidate_users = users_query.all()
    candidate_user_ids = [user.id for user in candidate_users]
    activity_user_ids = set()
    if candidate_user_ids:
        activity_user_ids.update(
            user_id
            for (user_id,) in db.session.query(ManualDailyRecord.user_id)
            .filter(
                ManualDailyRecord.user_id.in_(candidate_user_ids),
                ManualDailyRecord.record_date >= from_date,
                ManualDailyRecord.record_date <= to_date,
            )
            .distinct()
            .all()
        )
        activity_user_ids.update(
            user_id
            for (user_id,) in db.session.query(KaironChartRecord.user_id)
            .join(KaironUploadBatch, KaironChartRecord.batch_id == KaironUploadBatch.id)
            .filter(
                KaironUploadBatch.superseded_at.is_(None),
                KaironChartRecord.user_id.in_(candidate_user_ids),
                KaironChartRecord.completed_date >= from_date,
                KaironChartRecord.completed_date <= to_date,
            )
            .distinct()
            .all()
        )

    users = sorted(
        (
            user
            for user in candidate_users
            if user.is_active
            or (user.last_working_day is not None and user.last_working_day >= from_date)
            or (user.last_working_day is None and user.id in activity_user_ids)
        ),
        key=lambda user: (
            not user.is_active,
            (user.first_name or "").casefold(),
            (user.last_name or "").casefold(),
        ),
    )
    user_ids = [user.id for user in users]

    kairon_query = (
        db.session.query(KaironChartRecord.user_id, KaironChartRecord.status, func.count(KaironChartRecord.id))
        .join(KaironUploadBatch, KaironChartRecord.batch_id == KaironUploadBatch.id)
        .join(User, KaironChartRecord.user_id == User.id)
        .filter(
            KaironUploadBatch.superseded_at.is_(None),
            KaironChartRecord.status == "Completed",
            unique_completed_production(),
            KaironChartRecord.completed_date >= from_date,
            KaironChartRecord.completed_date <= to_date,
            db.or_(
                User.last_working_day.is_(None),
                KaironChartRecord.completed_date <= User.last_working_day,
            ),
            KaironChartRecord.user_id.isnot(None),
            KaironChartRecord.user_id.in_(user_ids),
        )
    )
    if program:
        kairon_query = kairon_query.filter(func.upper(KaironChartRecord.program) == program)
    kairon_rows = kairon_query.group_by(KaironChartRecord.user_id, KaironChartRecord.status).all()
    kairon_by_user = {}
    for user_id, status, count in kairon_rows:
        kairon_by_user.setdefault(user_id, {"active": 0, "on_hold": 0, "completed": 0})
        kairon_by_user[user_id][{"Active": "active", "On Hold": "on_hold", "Completed": "completed"}[status]] = count

    pvp_expression = ManualDailyRecord.pvp_count if program != "FOUNDATION" else 0
    foundation_expression = ManualDailyRecord.foundation_count if program != "PVP" else 0
    production_expression = pvp_expression + foundation_expression
    manual_rows = (
        db.session.query(
            ManualDailyRecord.user_id,
            func.sum(production_expression),
            func.sum(pvp_expression),
            func.sum(foundation_expression),
            func.sum(ManualDailyRecord.tech_issues_downtime_hours),
            func.sum(ManualDailyRecord.no_inventory_idle_time_hours),
            func.sum(ManualDailyRecord.leave_hours),
            func.sum(ManualDailyRecord.meeting_engagement_hours),
            func.sum(case((ManualDailyRecord.status == "pending", 1), else_=0)),
            func.count(ManualDailyRecord.id),
        )
        .join(User, ManualDailyRecord.user_id == User.id)
        .filter(
            ManualDailyRecord.record_date >= from_date,
            ManualDailyRecord.record_date <= to_date,
            db.or_(
                User.last_working_day.is_(None),
                ManualDailyRecord.record_date <= User.last_working_day,
            ),
            ManualDailyRecord.user_id.in_(user_ids),
        )
        .group_by(ManualDailyRecord.user_id)
        .all()
    )
    manual_by_user = {
        user_id: {
            "production_count": production_count,
            "pvp_count": pvp_count,
            "foundation_count": foundation_count,
            "tech_issues_downtime_hours": tech_hours,
            "no_inventory_idle_time_hours": idle_hours,
            "leave_hours": leave_hours,
            "meeting_engagement_hours": meeting_hours,
            "pending_count": pending_count,
            "record_count": record_count,
        }
        for (
            user_id,
            production_count,
            pvp_count,
            foundation_count,
            tech_hours,
            idle_hours,
            leave_hours,
            meeting_hours,
            pending_count,
            record_count,
        ) in manual_rows
    }
    efficiency_by_user = get_efficiency(
        user_ids,
        from_date,
        to_date,
        include_daily=include_daily,
        program=program,
    )

    return [
        {
            "user_id": user.id,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "is_active": user.is_active,
            "last_working_day": user.last_working_day,
            "lead_id": user.reports_to_id,
            "kairon": kairon_by_user.get(user.id, {"active": 0, "on_hold": 0, "completed": 0}),
            "manual": manual_by_user.get(
                user.id,
                {
                    "production_count": 0,
                    "pvp_count": 0,
                    "foundation_count": 0,
                    "tech_issues_downtime_hours": 0,
                    "no_inventory_idle_time_hours": 0,
                    "leave_hours": 0,
                    "meeting_engagement_hours": 0,
                    "pending_count": 0,
                    "record_count": 0,
                },
            ),
            "efficiency": efficiency_by_user[user.id],
        }
        for user in users
    ]
