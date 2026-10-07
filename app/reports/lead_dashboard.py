"""Lead dashboard: personal QA data and directly reporting coders stay separate."""
from collections import defaultdict
from decimal import Decimal

from flask_smorest import abort

from app.extensions import db
from app.reports.services import _cpd, _percent, get_efficiency, get_period_goal
from app.roles.models import Role, RoleType
from app.users.models import User


def _sum_known(rows, key):
    values = [row[key] for row in rows if row.get(key) is not None]
    return sum(values) if values else None


def _rates(row):
    target = row["adjusted_target"]
    minutes = row["target_minutes"]
    row.update(
        manual_efficiency_percent=_percent(row["manual_charts"], target) if target is not None else None,
        kairon_efficiency_percent=_percent(row["kairon_charts"], target) if target is not None else None,
        manual_cpd=_cpd(row["manual_charts"], minutes),
        kairon_cpd=_cpd(row["kairon_charts"], minutes),
        target_cpd=_cpd(target, minutes) if target is not None else None,
    )
    return row


def rollup_efficiency(summaries, from_date, to_date):
    """Sum source totals, then compute weighted CPD and capped efficiency.

    Never average already-rounded/capped percentages or add normalized CPDs.
    Saved adjusted CPD is additive chart capacity; missing snapshots stay
    missing when no contributing manual record supplies one.
    """
    total = {
        key: sum((summary[key] for summary in summaries), 0)
        for key in (
            "manual_charts", "kairon_charts", "inside_minutes", "login_days",
            "productive_minutes", "target_minutes", "calculated_days",
        )
    }
    total.update(
        from_date=from_date,
        to_date=to_date,
        adjusted_target=sum((summary["adjusted_target"] for summary in summaries), Decimal("0.00")),
        adjusted_cpd=_sum_known(summaries, "adjusted_cpd"),
    )
    by_day = defaultdict(list)
    for summary in summaries:
        for row in summary["daily"]:
            by_day[row["date"]].append(row)
    daily = []
    for day, rows in sorted(by_day.items(), reverse=True):
        stages = {row["stage"] for row in rows}
        row = {key: _sum_known(rows, key) for key in (
            "daily_target", "manual_charts", "kairon_charts", "inside_minutes",
            "downtime_minutes", "idle_minutes", "leave_minutes", "meeting_minutes",
            "excluded_minutes", "productive_minutes", "target_minutes", "adjusted_target", "adjusted_cpd",
        )}
        row.update(
            date=day,
            stage=next(iter(stages)) if len(stages) == 1 else "Mixed stages",
            manual_status=rows[0]["manual_status"] if len(rows) == 1 else None,
        )
        daily.append(_rates(row))
    total["daily"] = daily
    return _rates(total)


def _goal_for_users(goal, user_ids, scope):
    users = [row for row in goal["users"] if row["user_id"] in user_ids]
    result = {**goal, "scope": scope, "user_count": len(users), "users": users}
    for key in ("manual_charts", "completed_charts", "target_charts", "difference", "eligible_days", "leave_days_excluded"):
        result[key] = sum(row[key] for row in users)
    for key in ("adjusted_target_charts", "adjusted_difference"):
        result[key] = sum((row[key] for row in users), Decimal("0.00"))
    return result


def get_lead_dashboard(lead, from_date, to_date, coder_id=None, goal_to_date=None):
    # Role + direct reporting line are enforced here, not trusted from the UI.
    coders = User.query.join(Role).join(RoleType).filter(
        User.reports_to_id == lead.id,
        User.id != lead.id,
        RoleType.code == "employee",
        db.or_(User.last_working_day.is_(None), User.last_working_day >= from_date),
    ).order_by(User.is_active.desc(), User.first_name, User.last_name, User.id).all()
    if coder_id is not None and coder_id not in {coder.id for coder in coders}:
        abort(404, message="Coder not found in your team for this period.")
    selected = [coder for coder in coders if coder_id is None or coder.id == coder_id]
    selected_ids = {coder.id for coder in selected}
    members = [lead, *selected]
    goals = get_period_goal(members, from_date, goal_to_date or to_date)
    efficiency = get_efficiency([member.id for member in members], from_date, to_date, include_daily=True)
    return {
        "from_date": from_date,
        "to_date": to_date,
        "selected_coder_id": coder_id,
        "coder_options": [{"user_id": coder.id, "name": f"{coder.first_name} {coder.last_name}".strip(), "is_active": coder.is_active} for coder in coders],
        "qa": {"goal": _goal_for_users(goals, {lead.id}, "self"), "efficiency": efficiency[lead.id]},
        "coders": {
            "goal": _goal_for_users(goals, selected_ids, "team"),
            "efficiency": rollup_efficiency([efficiency[coder.id] for coder in selected], from_date, to_date),
        },
    }
