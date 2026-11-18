"""Who can see and act on IT tickets and practices.

- employee / lead: only their own tickets (role shown as Coder (1LR) / QA (2LR)).
- manager: tickets of every user in the manager's own project.
- admin / super_admin: everything; what they can do is governed only by
  the `it_tickets` / `practices` feature permissions on their role.
"""
from flask_smorest import abort

from app.auth import has_feature
from app.extensions import db
from app.roles.models import Role, RoleType
from app.users.models import User

OWN_ONLY_ROLE_TYPES = ("employee", "lead")
OVERSIGHT_ROLE_TYPES = ("manager", "admin", "super_admin")
ROLE_BY_TYPE = {"employee": "coder", "lead": "qa"}
TYPE_BY_ROLE = {"coder": "employee", "qa": "lead"}


def role_type(user):
    return user.role.role_type.code if user is not None and user.role is not None else None


def ticket_role(user):
    """'coder' (Coder (1LR)) or 'qa' (QA (2LR)); None for anyone else."""
    return ROLE_BY_TYPE.get(role_type(user))


def is_oversight(user):
    return role_type(user) in OVERSIGHT_ROLE_TYPES


def can_moderate_tickets(user):
    """Change any visible ticket's status, delete tickets, import tickets."""
    return is_oversight(user) and has_feature(user, "it_tickets", "write")


def can_manage_practices(user):
    return is_oversight(user) and has_feature(user, "practices", "write")


def visible_user_ids_query(user):
    """A SELECT of the user ids whose tickets `user` may see, or None for 'everyone'."""
    code = role_type(user)
    if code in OWN_ONLY_ROLE_TYPES:
        return db.select(db.literal(user.id))
    if code == "manager":
        if user.project_id is None:
            return db.select(User.id).where(db.false())
        return db.select(User.id).where(User.project_id == user.project_id)
    if code in ("admin", "super_admin"):
        return None
    abort(403, message="IT tickets are not available for this role.")


def apply_user_scope(query, user_column, user, scope="all"):
    if scope == "mine" or role_type(user) in OWN_ONLY_ROLE_TYPES:
        return query.filter(user_column == user.id)
    visible = visible_user_ids_query(user)
    return query if visible is None else query.filter(user_column.in_(visible))


def role_filter(query, user_join_column, role):
    """Restrict to Coder (1LR) / QA (2LR) people; `user_join_column` is the users.id column joined."""
    if role in (None, "", "all"):
        return query
    code = TYPE_BY_ROLE.get(role)
    if code is None:
        abort(422, message="role must be coder, qa or all.")
    subquery = (db.select(User.id).join(Role, Role.id == User.role_id)
                .join(RoleType, RoleType.id == Role.role_type_id).where(RoleType.code == code))
    return query.filter(user_join_column.in_(subquery))


def practice_scope(query, practice_project_column, user):
    """Managers see practices of their own project; admins see all."""
    code = role_type(user)
    if code == "manager":
        return query.filter(practice_project_column == user.project_id) if user.project_id else query.filter(db.false())
    if code in ("admin", "super_admin"):
        return query
    abort(403, message="Practices management requires a manager or admin role.")


def full_name(user):
    return " ".join(part for part in ((user.first_name or "").strip(), (user.last_name or "").strip()) if part)


def person(user):
    return {"id": user.id, "name": full_name(user), "empId": user.emp_id, "role": ticket_role(user)}
