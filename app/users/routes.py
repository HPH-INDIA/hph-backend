import secrets
from datetime import datetime, timedelta, timezone

from flask import current_app, g
from flask.views import MethodView
from flask_smorest import abort
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from app.auth import has_feature, require_feature, require_role
from app.encryption.passwords import InvalidCredentialsError, hash_password, verify_password
from app.extensions import db
from app.mailing.services import send_password_changed_confirmation, send_temporary_password_email
from app.responses import MessageEnvelopeSchema
from app.roles.models import Role, RoleType
from app.sessions.models import Session
from app.sessions.schemas import LoginEnvelopeSchema
from app.sessions.services import issue_session
from app.users import bp
from app.users.hierarchy import can_manage_role_type
from app.users.models import User
from app.users.schemas import (
    DeactivateUserSchema,
    SetPasswordSchema,
    ManagerTeamEnvelopeSchema,
    ManagerTeamQuerySchema,
    TeamAssignmentSchema,
    UserCreateSchema,
    UserEnvelopeSchema,
    UserFilterQuerySchema,
    UserListEnvelopeSchema,
    validate_project_for_role,
    validate_reporting_line,
)


def _visible_users():
    """Super Admin accounts never appear in user lists or selection options."""
    return User.query.join(Role).join(RoleType).filter(RoleType.code != "super_admin")


def _page(query, page, page_size):
    total = query.order_by(None).count()
    return {
        "items": query.offset((page - 1) * page_size).limit(page_size).all(),
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size,
    }


def _abort_duplicate_field(exc):
    # Both email and emp_id are unique; report which one actually conflicted
    # instead of always blaming email.
    detail = str(exc.orig).lower()
    if "emp_id" in detail:
        abort(409, message="A user with this employee id already exists.")
    abort(409, message="A user with this email already exists.")


def _enforce_hierarchy(action, target_role):
    actor_role_type = g.user.role.role_type
    if not can_manage_role_type(actor_role_type.code, target_role.role_type.code):
        abort(
            403,
            message=f"You are not permitted to {action} a user with the "
            f"'{target_role.role_type.label}' role type.",
        )


def _enforce_reporting_scope(target_role, reports_to_id):
    """Managers may only place users inside their own reporting tree."""
    if g.user.role.role_type.code != "manager":
        return
    target_code = target_role.role_type.code
    if target_code == "lead" and reports_to_id != g.user.id:
        abort(403, message="A manager may only assign leads to their own team.")
    if target_code == "employee":
        lead = db.session.get(User, reports_to_id)
        if lead is None or lead.reports_to_id != g.user.id:
            abort(403, message="A manager may only assign employees to a lead in their own team.")


def _revoke_active_sessions(user):
    now = datetime.now(timezone.utc)
    Session.query.filter_by(user_id=user.id).filter(Session.revoked_at.is_(None)).update(
        {"revoked_at": now}, synchronize_session=False
    )


def _issue_temp_password(user, is_resend=False):
    """Generates a fresh random temporary password, hashes and stores it,
    and hands the plaintext to the Mailing System (§5) — never logged,
    never returned in any API response.
    """
    plaintext = secrets.token_urlsafe(12)
    user.password_hash = hash_password(plaintext)
    user.first_login = True
    user.temp_password_expires_at = datetime.now(timezone.utc) + timedelta(
        hours=current_app.config["TEMP_PASSWORD_EXPIRY_HOURS"]
    )
    send_temporary_password_email(user, plaintext, is_resend=is_resend)


@bp.route("/users")
class Users(MethodView):
    @bp.response(200, UserListEnvelopeSchema)
    def get(self):
        # User management lists exclude Super Admin, including the caller.
        # Other roles see only their own non-Super-Admin profile.
        if has_feature(g.user, "user_management"):
            users = _visible_users().all()
        else:
            users = [] if g.user.role.role_type.code == "super_admin" else [g.user]
        return {"status": 200, "message": "Users retrieved successfully.", "data": users}

    @require_feature("user_management")
    @bp.arguments(UserCreateSchema)
    @bp.response(201, UserEnvelopeSchema)
    def post(self, data):
        role = db.session.get(Role, data["role_id"])
        _enforce_hierarchy("create/delete", role)
        _enforce_reporting_scope(role, data.get("reports_to_id"))

        user = User(
            email=data["email"],
            first_name=data["first_name"],
            last_name=data["last_name"],
            emp_id=data["emp_id"],
            role_id=data["role_id"],
            project_id=data.get("project_id"),
            reports_to_id=data.get("reports_to_id"),
            password_hash="",  # set by _issue_temp_password below
            is_active=True,
            created_by_id=g.user.id,
        )
        db.session.add(user)
        try:
            db.session.flush()  # assigns user.id (needed below); also surfaces a duplicate email/emp_id
        except IntegrityError as exc:
            db.session.rollback()
            _abort_duplicate_field(exc)

        _issue_temp_password(user)
        db.session.commit()
        return {"status": 201, "message": "User created successfully.", "data": user}


@bp.route("/users/active")
class ActiveUsers(MethodView):
    @require_feature("user_management")
    @bp.response(200, UserListEnvelopeSchema)
    def get(self):
        users = _visible_users().filter(User.is_active.is_(True)).all()
        return {"status": 200, "message": "Active users retrieved successfully.", "data": users}


@bp.route("/users/inactive")
class InactiveUsers(MethodView):
    @require_feature("user_management")
    @bp.response(200, UserListEnvelopeSchema)
    def get(self):
        users = _visible_users().filter(User.is_active.is_(False)).all()
        return {"status": 200, "message": "Inactive users retrieved successfully.", "data": users}


@bp.route("/users/filter")
class FilteredUsers(MethodView):
    # Ungated beyond the normal session-login requirement (the frontend is
    # responsible for only surfacing this to callers who should see it) -
    # unlike /users, which narrows to self for anyone without
    # user_management rather than filtering by project/role.
    @bp.arguments(UserFilterQuerySchema, location="query")
    @bp.response(200, UserListEnvelopeSchema)
    def get(self, args):
        query = _visible_users()
        if args.get("project_ids"):
            query = query.filter(User.project_id.in_(args["project_ids"]))
        if args.get("role_ids"):
            query = query.filter(User.role_id.in_(args["role_ids"]))
        users = query.all()
        return {"status": 200, "message": "Users retrieved successfully.", "data": users}


def _manager_leads(manager):
    return (
        User.query.join(Role)
        .join(RoleType)
        .filter(
            User.reports_to_id == manager.id,
            User.project_id == manager.project_id,
            User.is_active.is_(True),
            RoleType.code == "lead",
        )
        .order_by(User.first_name, User.last_name, User.id)
        .all()
    )


def _manager_coder_query(manager, lead_ids):
    return User.query.join(Role).join(RoleType).filter(
        User.project_id == manager.project_id,
        User.is_active.is_(True),
        RoleType.code == "employee",
        or_(User.reports_to_id.is_(None), User.reports_to_id.in_(lead_ids)),
    )


@bp.route("/teams/mine")
class MyManagerTeam(MethodView):
    @require_feature("user_management")
    @require_role("manager")
    @bp.arguments(ManagerTeamQuerySchema, location="query")
    @bp.response(200, ManagerTeamEnvelopeSchema)
    def get(self, args):
        leads = _manager_leads(g.user)
        lead_ids = [lead.id for lead in leads]
        base_query = _manager_coder_query(g.user, lead_ids)
        total_coders = base_query.order_by(None).count()
        unassigned_count = base_query.filter(User.reports_to_id.is_(None)).order_by(None).count()
        counts = dict(
            db.session.query(User.reports_to_id, db.func.count(User.id))
            .join(Role)
            .join(RoleType)
            .filter(
                User.project_id == g.user.project_id,
                User.is_active.is_(True),
                RoleType.code == "employee",
                User.reports_to_id.in_(lead_ids),
            )
            .group_by(User.reports_to_id)
            .all()
        )

        query = base_query
        lead_id = args.get("lead_id")
        if lead_id == 0:
            query = query.filter(User.reports_to_id.is_(None))
        elif lead_id is not None:
            if lead_id not in lead_ids:
                abort(400, message="leadId must reference a lead in your team.")
            query = query.filter(User.reports_to_id == lead_id)
        if args.get("search"):
            pattern = f"%{args['search'].strip()}%"
            query = query.filter(
                or_(
                    User.first_name.ilike(pattern),
                    User.last_name.ilike(pattern),
                    User.email.ilike(pattern),
                    User.emp_id.ilike(pattern),
                )
            )
        query = query.order_by(User.first_name, User.last_name, User.id)
        lead_summaries = [
            {
                "id": lead.id,
                "first_name": lead.first_name,
                "last_name": lead.last_name,
                "emp_id": lead.emp_id,
                "coder_count": counts.get(lead.id, 0),
            }
            for lead in leads
        ]
        return {
            "status": 200,
            "message": "Manager team retrieved successfully.",
            "data": {
                "leads": lead_summaries,
                "coders": _page(query, args["page"], args["page_size"]),
                "total_coders": total_coders,
                "unassigned_count": unassigned_count,
            },
        }


@bp.route("/teams/mine/assign")
class AssignManagerTeam(MethodView):
    @require_feature("user_management")
    @require_role("manager")
    @bp.arguments(TeamAssignmentSchema)
    @bp.response(200, UserListEnvelopeSchema)
    def post(self, data):
        leads = _manager_leads(g.user)
        lead_ids = {lead.id for lead in leads}
        if data["lead_id"] not in lead_ids:
            abort(400, message="The destination must be an active lead in your team.")

        coder_ids = list(dict.fromkeys(data["coder_ids"]))
        coders = _manager_coder_query(g.user, lead_ids).filter(User.id.in_(coder_ids)).all()
        if len(coders) != len(coder_ids):
            abort(403, message="One or more selected coders are outside your team.")
        for coder in coders:
            coder.reports_to_id = data["lead_id"]
        db.session.commit()
        return {"status": 200, "message": "Coder assignments updated successfully.", "data": coders}


@bp.route("/users/<int:user_id>")
class UserDetail(MethodView):
    @bp.response(200, UserEnvelopeSchema)
    def get(self, user_id):
        if not has_feature(g.user, "user_management") and user_id != g.user.id:
            abort(403, message="You are not permitted to view this user.")
        user = User.query.get_or_404(user_id)
        return {"status": 200, "message": "User retrieved successfully.", "data": user}

    @require_feature("user_management")
    @bp.arguments(UserCreateSchema(partial=True))
    @bp.response(200, UserEnvelopeSchema)
    def patch(self, data, user_id):
        user = User.query.get_or_404(user_id)
        _enforce_hierarchy("manage", user.role)

        new_role = user.role
        if "role_id" in data:
            new_role = db.session.get(Role, data["role_id"])
            if new_role is None:
                abort(400, message="role_id must reference an existing role.")
            if new_role.role_type_id != user.role.role_type_id:
                _enforce_hierarchy("manage", new_role)

        effective_project_id = data["project_id"] if "project_id" in data else user.project_id
        effective_reports_to_id = data["reports_to_id"] if "reports_to_id" in data else user.reports_to_id
        try:
            validate_project_for_role(new_role, effective_project_id)
            validate_reporting_line(new_role, effective_project_id, effective_reports_to_id, user.id)
        except ValueError as exc:
            abort(400, message=str(exc))
        _enforce_reporting_scope(new_role, effective_reports_to_id)

        for key in ("email", "first_name", "last_name", "emp_id", "role_id", "project_id", "reports_to_id"):
            if key in data:
                setattr(user, key, data[key])

        try:
            db.session.commit()
        except IntegrityError as exc:
            db.session.rollback()
            _abort_duplicate_field(exc)
        return {"status": 200, "message": "User updated successfully.", "data": user}

    @require_feature("user_management")
    @bp.arguments(DeactivateUserSchema)
    @bp.response(200, MessageEnvelopeSchema)
    def delete(self, data, user_id):
        user = User.query.get_or_404(user_id)
        _enforce_hierarchy("create/delete", user.role)

        # soft-delete, mirroring §1a's Feature "delete means deactivate" —
        # a hard delete would orphan sessions and other users' created_by_id
        user.is_active = False
        user.last_working_day = data["last_working_day"]
        _revoke_active_sessions(user)
        db.session.commit()
        return {"status": 200, "message": "User deleted successfully.", "data": None}


@bp.route("/users/<int:user_id>/resend-temporary-password")
class ResendTemporaryPassword(MethodView):
    @require_feature("user_management")
    @bp.response(200, UserEnvelopeSchema)
    def post(self, user_id):
        user = User.query.get_or_404(user_id)
        _enforce_hierarchy("manage", user.role)

        _issue_temp_password(user, is_resend=True)
        _revoke_active_sessions(user)
        db.session.commit()
        return {
            "status": 200,
            "message": "A new temporary password has been issued.",
            "data": user,
        }


@bp.route("/users/set-password")
class SetPassword(MethodView):
    @bp.arguments(SetPasswordSchema)
    @bp.response(200, LoginEnvelopeSchema)
    def post(self, data):
        user = g.user
        try:
            verify_password(user.password_hash, data["current_password"])
        except InvalidCredentialsError:
            abort(401, message="Current password is incorrect.")

        user.password_hash = hash_password(data["new_password"])
        user.first_login = False
        user.temp_password_expires_at = None

        _revoke_active_sessions(user)
        db.session.commit()
        send_password_changed_confirmation(user)

        issue_session(user)
        return {"status": 200, "message": "Password updated successfully.", "data": {"user": user}}
