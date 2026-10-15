"""Project performance for clients; no reporting hierarchy or attendance payload."""
from flask_smorest import abort
from decimal import Decimal
from app.extensions import db
from app.auth import has_feature
from app.reports.services import get_efficiency, get_period_goal
from app.reports.lead_dashboard import rollup_efficiency
from app.roles.models import Role, RoleType
from app.users.models import User
from app.kairon.models import KaironChartRecord, KaironUploadBatch
from app.kairon.production import unique_completed_production
from app.manual_daily_records.models import ManualDailyRecord


def get_client_dashboard(client, from_date, to_date, goal_to_date=None, program=None, user_id=None):
    rows = (db.session.query(User, RoleType.code).select_from(User).join(Role).join(RoleType)
            .filter(User.project_id == client.project_id, RoleType.code.in_(["lead", "employee"]),
                    db.or_(User.last_working_day.is_(None), User.last_working_day >= from_date))
            .order_by(User.first_name, User.last_name, User.id).all())
    if user_id is not None:
        rows = [(user, role) for user, role in rows if user.id == user_id]
        if not rows:
            abort(404, message="Person not found in your project for this period.")
    result = {"from_date": from_date, "to_date": to_date, "members": []}
    for key, role, level in [("qa", "lead", "2LR"), ("coders", "employee", "1LR")]:
        users = [user for user, code in rows if code == role]
        efficiency = get_efficiency([user.id for user in users], from_date, to_date,
                                    include_daily=True, program=program, level=level)
        result[key] = {
            "goal": get_period_goal(users, from_date, goal_to_date or to_date,
                                    scope=key, program=program, level=level),
            "efficiency": rollup_efficiency(list(efficiency.values()), from_date, to_date),
        }
        result["members"].extend({"user_id": user.id, "name": f"{user.first_name} {user.last_name}".strip(),
                                  "is_active": user.is_active, "role_type": role,
                                  "efficiency": efficiency[user.id]} for user in users)
    # Consolidate already level-scoped QA/coder data; preserve shared calendar
    # values once and recompute weighted rates from the raw source denominators.
    qa_goal, coder_goal = result["qa"]["goal"], result["coders"]["goal"]
    combined_goal = {**qa_goal, "scope": "project", "users": [*qa_goal["users"], *coder_goal["users"]]}
    for key in ("kairon_pvp_count", "kairon_foundation_count", "manual_pvp_count", "manual_foundation_count", "user_count", "manual_charts", "completed_charts", "target_charts", "difference",
                "eligible_days", "leave_days_excluded"):
        combined_goal[key] = qa_goal[key] + coder_goal[key]
    for key in ("adjusted_target_charts", "adjusted_difference"):
        combined_goal[key] = sum((goal[key] for goal in (qa_goal, coder_goal)), Decimal("0.00"))
    result["overview"] = {"goal": combined_goal,
        "efficiency": rollup_efficiency([member["efficiency"] for member in result["members"]], from_date, to_date)}
    if user_id is not None:
        person, role = rows[0]
        level = "2LR" if role == "lead" else "1LR"
        query = KaironChartRecord.query.join(KaironUploadBatch).filter(
            KaironUploadBatch.superseded_at.is_(None), KaironChartRecord.user_id == person.id,
            KaironChartRecord.status == "Completed", unique_completed_production(),
            KaironChartRecord.level == level, KaironChartRecord.completed_date >= from_date,
            KaironChartRecord.completed_date <= to_date)
        if person.last_working_day:
            query = query.filter(KaironChartRecord.completed_date <= person.last_working_day)
        if program:
            query = query.filter(db.func.upper(KaironChartRecord.program) == program)
        result["charts"] = query.order_by(KaironChartRecord.completed_date.desc(), KaironChartRecord.id).all()
        manual = ManualDailyRecord.query.filter(ManualDailyRecord.user_id == person.id,
            ManualDailyRecord.record_date >= from_date, ManualDailyRecord.record_date <= to_date)
        if person.last_working_day:
            manual = manual.filter(ManualDailyRecord.record_date <= person.last_working_day)
        result["records"] = manual.order_by(ManualDailyRecord.record_date.desc()).all()
        if has_feature(client, "chart_holds"):
            result["holds"] = KaironChartRecord.query.join(KaironUploadBatch).filter(
                KaironUploadBatch.superseded_at.is_(None), KaironChartRecord.user_id == person.id,
                KaironChartRecord.status == "On Hold", KaironChartRecord.level == level
            ).order_by(KaironChartRecord.created_date.desc(), KaironChartRecord.id).all()
    return result


def restrict_client_cards(cards):
    from app.reports.schemas import CLIENT_DAILY_FIELDS, CLIENT_SUMMARY_FIELDS
    for card in cards:
        for key in ("email", "lead_id", "last_working_day"):
            card.pop(key, None)
        efficiency = card["efficiency"]
        for key in list(efficiency):
            if key not in (*CLIENT_SUMMARY_FIELDS, "daily"):
                efficiency.pop(key)
        efficiency["daily"] = [{key: value for key, value in day.items() if key in CLIENT_DAILY_FIELDS}
                               for day in efficiency.get("daily", [])]
