# Which RoleType.code an acting user may create/delete/manage a target user
# under, independent of any Role's feature list. Not derived from
# hierarchy_rank — lead/employee are hard-blocked even though employee has
# nothing "below" it, and no one (including super_admin) may create/delete a
# super_admin, so it never appears as a value here.
CAN_MANAGE_ROLE_TYPE = {
    "super_admin": {"admin", "manager", "lead", "employee"},
    "admin": {"manager", "lead", "employee"},
    "manager": {"lead", "employee"},
    "lead": set(),
    "employee": set(),
}


def can_manage_role_type(actor_role_type_code, target_role_type_code):
    return target_role_type_code in CAN_MANAGE_ROLE_TYPE.get(actor_role_type_code, set())


def lead_employee_user_ids(lead_id):
    """Employees reporting directly to a lead, including historical members."""
    from app.roles.models import Role, RoleType
    from app.users.models import User

    return [
        user.id
        for user in User.query.join(Role).join(RoleType).filter(
            User.reports_to_id == lead_id,
            RoleType.code == "employee",
        )
    ]


def report_lead_ids(viewer):
    """Lead teams currently visible for reports; never alter the org hierarchy."""
    from app.lead_coverage.services import active_covered_lead_ids

    return list(dict.fromkeys([viewer.id, *active_covered_lead_ids(viewer)]))


def report_team_user_ids(viewer, *, include_leads=True, lead_id=None):
    """Current report/review scope, independent of the requested data period."""
    from app.roles.models import Role, RoleType
    from app.users.models import User

    lead_ids = report_lead_ids(viewer)
    if lead_id is not None:
        resolve_report_lead(viewer, lead_id)
        lead_ids = [lead_id]
    employee_ids = [user.id for user in User.query.join(Role).join(RoleType).filter(
        User.reports_to_id.in_(lead_ids), RoleType.code == "employee",
    )]
    return list(dict.fromkeys([*(lead_ids if include_leads else []), *employee_ids]))


def resolve_report_lead(viewer, lead_id=None):
    """Validate a source team on every read, including dashboard drilldowns."""
    from flask_smorest import abort
    from app.extensions import db
    from app.users.models import User

    selected_id = viewer.id if lead_id is None else lead_id
    if selected_id not in report_lead_ids(viewer):
        abort(404, message="Lead team not found or coverage is no longer active.")
    return db.session.get(User, selected_id)


def report_team_options(viewer):
    from app.users.models import User

    ids = report_lead_ids(viewer)
    members = {user.id: user for user in User.query.filter(User.id.in_(ids))}
    return [{"lead_id": lead_id, "name": f"{members[lead_id].first_name} {members[lead_id].last_name}".strip()}
            for lead_id in ids if lead_id in members]


def manager_team_user_ids(manager_id):
    """Return a manager's leads and employees.

    Existing projects with exactly one manager may still have employees that
    predate reporting-line assignments. Those employees are unambiguously in
    that manager's project team and remain visible during the transition.
    """
    from app.extensions import db
    from app.roles.models import Role, RoleType
    from app.users.models import User

    lead_ids = [
        user.id
        for user in User.query.join(Role).join(RoleType).filter(
            User.reports_to_id == manager_id,
            RoleType.code == "lead",
        )
    ]
    employee_ids = [
        user.id
        for user in User.query.join(Role).join(RoleType).filter(
            User.reports_to_id.in_(lead_ids),
            RoleType.code == "employee",
        )
    ]
    manager = db.session.get(User, manager_id)
    if manager is not None and manager.project_id is not None:
        manager_count = (
            User.query.join(Role)
            .join(RoleType)
            .filter(
                User.project_id == manager.project_id,
                User.is_active.is_(True),
                RoleType.code == "manager",
            )
            .count()
        )
        if manager_count == 1:
            transitional_employee_ids = [
                user.id
                for user in User.query.join(Role).join(RoleType).filter(
                    User.project_id == manager.project_id,
                    User.reports_to_id.is_(None),
                    RoleType.code == "employee",
                )
            ]
            employee_ids.extend(transitional_employee_ids)
    return list(dict.fromkeys(lead_ids + employee_ids))


def manager_lead_team_user_ids(manager_id, lead_id):
    """Return one manager-owned lead plus the employees reporting to them.

    ``None`` means the requested lead is not an active direct report of the
    manager. Keeping that distinction lets API callers reject an invalid
    lead filter instead of quietly returning an empty result.
    """
    from app.roles.models import Role, RoleType
    from app.users.models import User

    lead = (
        User.query.join(Role)
        .join(RoleType)
        .filter(
            User.id == lead_id,
            User.reports_to_id == manager_id,
            User.is_active.is_(True),
            RoleType.code == "lead",
        )
        .first()
    )
    if lead is None:
        return None

    employee_ids = [
        user.id
        for user in User.query.join(Role).join(RoleType).filter(
            User.reports_to_id == lead_id,
            User.is_active.is_(True),
            RoleType.code == "employee",
        )
    ]
    return [lead.id, *employee_ids]
