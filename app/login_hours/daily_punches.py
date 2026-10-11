"""Today's manager report comes only from the latest complete CODING sheet."""
from sqlalchemy import cast
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import joinedload

from app.cohorts.progression import business_today
from app.extensions import db
from app.login_hours.models import LoginHoursUploadBatch
from app.roles.models import Role, RoleType
from app.users.models import User


def today_punch_report(actor):
    day = business_today()
    empty = dict(date=day, available=False, uploaded_at=None, source_filename=None,
                 total_users=0, punched_users=0, missing_users=[], unmatched_count=0)
    if actor.project is None or actor.project.name != "CODING":
        return empty
    # Attendance imports only accept CODING rows. Project managers/leads or
    # organization administrators can upload the sheet for this project.
    batch = LoginHoursUploadBatch.query.join(User, User.id == LoginHoursUploadBatch.uploaded_by_id).join(Role).join(RoleType).filter(
        LoginHoursUploadBatch.completed_at.isnot(None),
        cast(LoginHoursUploadBatch.attendance_dates, JSONB).contains([day.isoformat()]),
        db.or_(User.project_id == actor.project_id, RoleType.code.in_(("admin", "super_admin"))),
    ).order_by(LoginHoursUploadBatch.completed_at.desc(), LoginHoursUploadBatch.id.desc()).first()
    if batch is None:
        return empty
    people = User.query.join(Role).join(RoleType).filter(
        User.project_id == actor.project_id, RoleType.code.in_(("lead", "employee")), User.is_active.is_(True),
        db.or_(User.join_date.is_(None), User.join_date <= day),
        db.or_(User.last_working_day.is_(None), User.last_working_day >= day),
    ).options(joinedload(User.role).joinedload(Role.role_type)).order_by(User.first_name, User.last_name, User.id).all()
    punched = set((batch.punch_user_ids_by_date or {}).get(day.isoformat(), []))
    missing = [dict(id=u.id, first_name=u.first_name, last_name=u.last_name, emp_id=u.emp_id,
                    role_type=u.role.role_type.code) for u in people if u.id not in punched]
    return dict(date=day, available=True, uploaded_at=batch.completed_at, source_filename=batch.source_filename,
                total_users=len(people), punched_users=len(people)-len(missing), missing_users=missing,
                unmatched_count=batch.unmatched_count)
