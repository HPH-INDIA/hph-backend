"""Manager performance, scoped to reporting lines and batched across teams."""
from flask_smorest import abort

from app.cohorts.models import Cohort, CohortMembership
from app.extensions import db
from app.reports.lead_dashboard import _goal_for_users, rollup_efficiency
from app.reports.services import get_efficiency, get_period_goal
from app.roles.models import Role, RoleType
from app.users.hierarchy import manager_team_user_ids
from app.users.models import User


def get_manager_dashboard(manager, from_date, to_date, lead_id=None, coder_id=None,
                          cohort_id=None, program=None, goal_to_date=None):
    rows = (db.session.query(User, RoleType.code).select_from(User).join(Role).join(RoleType)
            .filter(User.id.in_(manager_team_user_ids(manager.id)))
            .order_by(User.is_active.desc(), User.first_name, User.last_name, User.id).all())
    leads = [user for user, role in rows if role == "lead"]
    eligible = lambda user: user.last_working_day is None or user.last_working_day >= from_date
    coders = [user for user, role in rows if role == "employee" and eligible(user)]
    lead_ids = {lead.id for lead in leads}
    memberships = (db.session.query(CohortMembership.user_id, Cohort.id, Cohort.label)
                   .join(Cohort, CohortMembership.cohort_id == Cohort.id)
                   .filter(CohortMembership.user_id.in_([user.id for user, _ in rows])).all())
    cohort_by_user = {user_id: cohort for user_id, cohort, _ in memberships}
    cohort_options = sorted({cohort: label for _, cohort, label in memberships}.items(), key=lambda row: row[1])
    if lead_id is not None and lead_id not in lead_ids:
        abort(404, message="Lead not found in your teams.")
    if cohort_id is not None and cohort_id not in {cohort for cohort, _ in cohort_options}:
        abort(404, message="Cohort not found in your teams.")

    def in_cohort(user):
        return cohort_id is None or cohort_by_user.get(user.id) == cohort_id

    available_coders = [coder for coder in coders if in_cohort(coder) and (lead_id is None or coder.reports_to_id == lead_id)]
    selected_coder = next((coder for coder in available_coders if coder.id == coder_id), None)
    if coder_id is not None and selected_coder is None:
        abort(404, message="Coder not found in the selected teams and cohort for this period.")
    selected_coders = [selected_coder] if selected_coder else available_coders
    # Selecting a coder also gives their team's QA context. QA never enters coder totals.
    selected_leads = [lead for lead in leads if eligible(lead) and in_cohort(lead)
                      and (lead_id is None or lead.id == lead_id)
                      and (selected_coder is None or lead.id == selected_coder.reports_to_id)]
    members = [*selected_leads, *selected_coders]
    goals = get_period_goal(members, from_date, goal_to_date or to_date, program=program)
    efficiency = get_efficiency([member.id for member in members], from_date, to_date, include_daily=True, program=program)

    def option(user):
        return {"user_id": user.id, "name": f"{user.first_name} {user.last_name}".strip(), "is_active": user.is_active}

    def section(users):
        return {"goal": _goal_for_users(goals, {user.id for user in users}, "team"),
                "efficiency": rollup_efficiency([efficiency[user.id] for user in users], from_date, to_date)}

    teams = []
    for lead in leads:
        team_coders = [coder for coder in selected_coders if coder.reports_to_id == lead.id]
        team_qa = [lead] if lead in selected_leads else []
        if team_coders or team_qa:
            teams.append({"key": f"lead-{lead.id}", "lead": option(lead), "qa": section(team_qa), "coders": section(team_coders)})
    unassigned = [coder for coder in selected_coders if coder.reports_to_id not in lead_ids]
    if unassigned:
        teams.append({"key": "unassigned", "lead": None, "qa": section([]), "coders": section(unassigned)})
    return {
        "from_date": from_date, "to_date": to_date,
        "lead_options": [option(lead) for lead in leads if eligible(lead) or any(coder.reports_to_id == lead.id for coder in coders)],
        # Keep options independent of the selected filters so the drawer can switch teams locally.
        "coder_options": [{**option(coder), "lead_id": coder.reports_to_id if coder.reports_to_id in lead_ids else None,
                           "cohort_id": cohort_by_user.get(coder.id)} for coder in coders],
        "cohort_options": [{"id": cohort, "label": label} for cohort, label in cohort_options],
        "overall": rollup_efficiency(list(efficiency.values()), from_date, to_date),
        "qa": section(selected_leads), "coders": section(selected_coders), "teams": teams,
        "members": [{**option(user), "emp_id": user.emp_id, "role_type": "lead" if user in selected_leads else "employee",
                     "lead_id": user.id if user in selected_leads else user.reports_to_id,
                     "efficiency": {key: value for key, value in efficiency[user.id].items() if key != "daily"}}
                    for user in members],
    }
