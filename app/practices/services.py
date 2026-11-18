"""Practices master list: CRUD, status changes (Terminated resolves open
tickets), per-person assignments and coders' add-practice requests."""
from datetime import datetime, timezone

from flask_smorest import abort
from sqlalchemy.exc import IntegrityError

from app.cohorts.progression import business_today
from app.extensions import db
from app.it_tickets.access import (
    can_manage_practices, full_name, person, practice_scope, role_type, ticket_role,
)
from app.it_tickets.models import ItTicket, ItTicketEvent
from app.kairon.models import KaironChartRecord
from app.practices.models import (
    PRACTICE_STATUSES, Practice, PracticeAssignment, PracticeRequest, PracticeStatusChange, practice_name_key,
)
from app.roles.models import Role, RoleType
from app.users.models import Project, User

EDITABLE_FIELDS = (
    "name", "tin", "worktype", "priority_rank", "cms_count", "emr", "affiliation",
    "pipeline_2027", "filter_72", "onshore_coders", "notes",
)
TERMINATED_NOTE = "Practice terminated"


def clean_tin(value):
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits or None


def default_project_id(user):
    if user.project_id:
        return user.project_id
    coding = Project.query.filter(db.func.upper(Project.name) == "CODING").first()
    return coding.id if coding else None


# ---- serialisation ---------------------------------------------------------

def serialize_assignment(assignment):
    user = assignment.user
    return {
        "id": assignment.id, "practiceId": assignment.practice_id, "userId": user.id,
        "userName": full_name(user), "empId": user.emp_id, "role": ticket_role(user),
        "accessStatus": assignment.access_status,
        "accessStatusSince": assignment.access_status_since.isoformat() if assignment.access_status_since else None,
    }


def ticket_counts(practice_ids):
    if not practice_ids:
        return {}
    rows = db.session.execute(
        db.select(ItTicket.practice_id, ItTicket.status, db.func.count())
        .where(ItTicket.practice_id.in_(practice_ids)).group_by(ItTicket.practice_id, ItTicket.status)).all()
    counts = {}
    for practice_id, status, count in rows:
        entry = counts.setdefault(practice_id, {"open": 0, "total": 0})
        entry["total"] += count
        if status == "in_progress":
            entry["open"] += count
    return counts


def serialize_practice(practice, counts=None):
    counts = counts if counts is not None else ticket_counts([practice.id]).get(practice.id, {})
    return {
        "id": practice.id, "name": practice.name, "tin": practice.tin, "projectId": practice.project_id,
        "worktype": practice.worktype, "priorityRank": practice.priority_rank, "cmsCount": practice.cms_count,
        "emr": practice.emr, "affiliation": practice.affiliation, "pipeline2027": practice.pipeline_2027,
        "filter72": practice.filter_72, "onshoreCoders": practice.onshore_coders, "notes": practice.notes,
        "status": practice.status,
        "statusEffectiveFrom": practice.status_effective_from.isoformat() if practice.status_effective_from else None,
        "statusReason": practice.status_reason,
        "openTicketCount": counts.get("open", 0), "totalTicketCount": counts.get("total", 0),
        "assignments": [serialize_assignment(a) for a in practice.assignments],
        "updatedAt": practice.updated_at.isoformat() if practice.updated_at else None,
    }


# ---- queries ---------------------------------------------------------------

def scoped_practices(user):
    return practice_scope(Practice.query, Practice.project_id, user)


def get_practice(user, practice_id):
    practice = scoped_practices(user).filter(Practice.id == practice_id).first()
    if practice is None:
        abort(404, message="Practice not found.")
    return practice


def list_practices(user, args):
    query = scoped_practices(user)
    statuses = args.get("status") or ["Active", "Inactive"]
    query = query.filter(Practice.status.in_(statuses))
    if args.get("worktype"):
        query = query.filter(Practice.worktype == args["worktype"])
    if args.get("search"):
        term = f"%{args['search'].strip().lower()}%"
        query = query.filter(db.or_(Practice.name_key.like(term), Practice.tin.like(term)))
    if args.get("user_id") or args.get("access_status"):
        sub = db.select(PracticeAssignment.practice_id)
        if args.get("user_id"):
            sub = sub.where(PracticeAssignment.user_id.in_(args["user_id"]))
        if args.get("access_status"):
            sub = sub.where(PracticeAssignment.access_status.in_(args["access_status"]))
        query = query.filter(Practice.id.in_(sub))
    total = query.count()
    page, page_size = args.get("page", 1), args.get("page_size", 25)
    practices = (query.order_by(Practice.priority_rank.asc().nulls_last(), Practice.name_key.asc())
                 .offset((page - 1) * page_size).limit(page_size).all())
    counts = ticket_counts([p.id for p in practices])
    status_counts = dict(db.session.execute(
        scoped_practices(user).with_entities(Practice.status, db.func.count()).group_by(Practice.status)).all())
    pending = _requests_query(user).filter(PracticeRequest.status == "pending").count() if can_manage_practices(user) else 0
    return {
        "items": [serialize_practice(p, counts.get(p.id, {})) for p in practices],
        "total": total, "page": page, "pageSize": page_size,
        "counts": {"active": status_counts.get("Active", 0), "inactive": status_counts.get("Inactive", 0),
                   "terminated": status_counts.get("Terminated", 0), "pendingRequests": pending},
    }


def _check_unique_tin(tin, exclude_id=None):
    if not tin:
        return
    query = Practice.query.filter(Practice.tin == tin)
    if exclude_id:
        query = query.filter(Practice.id != exclude_id)
    if query.first() is not None:
        abort(409, message=f"Another practice already uses TIN {tin}.")


def _apply_fields(practice, data):
    for field in EDITABLE_FIELDS:
        if field in data:
            value = data[field]
            if isinstance(value, str):
                value = value.strip() or None
            setattr(practice, field, value)
    if "tin" in data:
        practice.tin = clean_tin(data["tin"])
    if not practice.name:
        abort(422, message="Practice name is required.")
    practice.name_key = practice_name_key(practice.name)


def link_kairon_records(practice):
    """Point unlinked Kairon chart records with the same practice name at this practice."""
    db.session.execute(
        db.update(KaironChartRecord)
        .where(KaironChartRecord.practice_id.is_(None),
               db.func.lower(db.func.regexp_replace(db.func.trim(KaironChartRecord.practice), r"\s+", " ", "g"))
               == practice.name_key)
        .values(practice_id=practice.id))


def link_all_kairon_records():
    """After a Kairon upload: link new chart records to practices by normalised name."""
    db.session.execute(db.text(
        "UPDATE kairon_chart_records k SET practice_id = p.id FROM practices p "
        "WHERE k.practice_id IS NULL AND k.practice IS NOT NULL "
        "AND lower(regexp_replace(btrim(k.practice), '\\s+', ' ', 'g')) = p.name_key"))


def build_practice(user, data, *, project_id=None):
    """A new, unsaved practice. No queries when project_id is given, so bulk
    callers can build many and flush once; TIN uniqueness is the caller's job."""
    status = data.get("status") or "Active"
    if status not in PRACTICE_STATUSES:
        abort(422, message=f"Status must be one of {', '.join(PRACTICE_STATUSES)}.")
    practice = Practice(project_id=project_id if project_id is not None else default_project_id(user),
                        created_by_id=user.id, updated_by_id=user.id,
                        status=status, status_effective_from=business_today())
    _apply_fields(practice, data)
    return practice


def created_status_change(user, practice):
    return PracticeStatusChange(practice_id=practice.id, from_status=None, to_status=practice.status,
                                effective_from=practice.status_effective_from, reason="Created", actor_id=user.id)


def create_practice(user, data, *, commit=True, link_kairon=True):
    practice = build_practice(user, data)
    _check_unique_tin(practice.tin)
    db.session.add(practice)
    db.session.flush()
    db.session.add(created_status_change(user, practice))
    if link_kairon:
        link_kairon_records(practice)
    if commit:
        db.session.commit()
    return practice


def update_practice(user, practice, data):
    _apply_fields(practice, data)
    _check_unique_tin(practice.tin, exclude_id=practice.id)
    practice.updated_by_id = user.id
    link_kairon_records(practice)
    db.session.commit()
    return practice


def delete_practice(practice):
    if ItTicket.query.filter_by(practice_id=practice.id).first() is not None:
        abort(409, message="This practice has tickets. Set its status to Terminated instead of deleting it.")
    db.session.delete(practice)
    db.session.commit()


def change_status(user, practice, status, effective_from, reason=None):
    if status not in PRACTICE_STATUSES:
        abort(422, message=f"Status must be one of {', '.join(PRACTICE_STATUSES)}.")
    effective_from = effective_from or business_today()
    previous = practice.status
    resolved = 0
    if status == "Terminated":
        open_tickets = ItTicket.query.filter_by(practice_id=practice.id, status="in_progress").with_for_update().all()
        for ticket in open_tickets:
            ticket.status = "resolved"
            ticket.resolved_on = max(effective_from, ticket.opened_on)
            ticket.resolution_note = TERMINATED_NOTE
            db.session.add(ItTicketEvent(
                ticket_id=ticket.id, jira_key=ticket.jira_key, action="auto_resolved", actor_id=user.id,
                reason=TERMINATED_NOTE,
                changes={"status": ["in_progress", "resolved"], "resolvedOn": ticket.resolved_on.isoformat()}))
        resolved = len(open_tickets)
    practice.status = status
    practice.status_effective_from = effective_from
    practice.status_reason = (reason or "").strip() or None
    practice.updated_by_id = user.id
    db.session.add(PracticeStatusChange(practice_id=practice.id, from_status=previous, to_status=status,
                                        effective_from=effective_from, reason=practice.status_reason,
                                        auto_resolved_count=resolved, actor_id=user.id))
    db.session.commit()
    return resolved


def status_history(practice):
    rows = (PracticeStatusChange.query.filter_by(practice_id=practice.id)
            .order_by(PracticeStatusChange.created_at.desc(), PracticeStatusChange.id.desc()).all())
    return [{
        "id": row.id, "fromStatus": row.from_status, "toStatus": row.to_status,
        "effectiveFrom": row.effective_from.isoformat(), "reason": row.reason,
        "actorName": full_name(row.actor) if row.actor else None,
        "autoResolvedCount": row.auto_resolved_count, "createdAt": row.created_at.isoformat(),
    } for row in rows]


# ---- assignments -----------------------------------------------------------

def assignable_users_query(user):
    query = (User.query.join(Role, Role.id == User.role_id).join(RoleType, RoleType.id == Role.role_type_id)
             .filter(RoleType.code.in_(("employee", "lead")), User.is_active.is_(True)))
    if role_type(user) not in ("admin", "super_admin"):
        query = query.filter(User.project_id == user.project_id)
    return query


def assignable_users(user):
    return [person(u) for u in assignable_users_query(user).order_by(User.first_name, User.last_name).all()]


def _assignee(user, user_id):
    target = assignable_users_query(user).filter(User.id == user_id).first()
    if target is None:
        abort(422, message="Only active Coder (1LR) or QA (2LR) users can be assigned to a practice.")
    return target


def add_assignment(user, practice, data, *, commit=True):
    target = _assignee(user, data["user_id"])
    assignment = PracticeAssignment(
        practice_id=practice.id, user_id=target.id, access_status=data.get("access_status"),
        access_status_since=data.get("access_status_since") or (business_today() if data.get("access_status") else None),
        assigned_by_id=user.id)
    db.session.add(assignment)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        abort(409, message="This person is already assigned to the practice.")
    if commit:
        db.session.commit()
    return assignment


def get_assignment(user, assignment_id):
    assignment = db.session.get(PracticeAssignment, assignment_id)
    if assignment is None or scoped_practices(user).filter(Practice.id == assignment.practice_id).first() is None:
        abort(404, message="Assignment not found.")
    return assignment


def update_assignment(assignment, data):
    if "access_status" in data and data["access_status"] != assignment.access_status:
        assignment.access_status = data["access_status"]
        assignment.access_status_since = data.get("access_status_since") or business_today()
    elif "access_status_since" in data:
        assignment.access_status_since = data["access_status_since"]
    db.session.commit()
    return assignment


def delete_assignment(assignment):
    db.session.delete(assignment)
    db.session.commit()


def my_practices(user):
    rows = (db.session.query(Practice, PracticeAssignment)
            .join(PracticeAssignment, PracticeAssignment.practice_id == Practice.id)
            .filter(PracticeAssignment.user_id == user.id, Practice.status != "Terminated")
            .order_by(Practice.name_key).all())
    return [{"id": p.id, "name": p.name, "status": p.status, "accessStatus": a.access_status} for p, a in rows]


# ---- requests --------------------------------------------------------------

def _suggest(request_row, user):
    query = scoped_practices(user)
    tin = clean_tin(request_row.tin)
    if tin:
        match = query.filter(Practice.tin == tin).first()
        if match:
            return {"practiceId": match.id, "practiceName": match.name, "reason": "tin"}
    key = practice_name_key(request_row.name)
    match = query.filter(Practice.name_key == key).first() or query.filter(Practice.name_key.like(f"%{key}%")).first()
    return {"practiceId": match.id, "practiceName": match.name, "reason": "name"} if match else None


def serialize_request(request_row, viewer=None):
    return {
        "id": request_row.id, "requestedById": request_row.requested_by_id,
        "requestedByName": full_name(request_row.requested_by), "name": request_row.name, "tin": request_row.tin,
        "note": request_row.note, "status": request_row.status, "createdAt": request_row.created_at.isoformat(),
        "resolvedById": request_row.resolved_by_id,
        "resolvedAt": request_row.resolved_at.isoformat() if request_row.resolved_at else None,
        "resolutionNote": request_row.resolution_note, "practiceId": request_row.practice_id,
        "suggestion": _suggest(request_row, viewer) if viewer is not None and request_row.status == "pending" else None,
    }


def _requests_query(user):
    query = PracticeRequest.query.join(User, User.id == PracticeRequest.requested_by_id)
    if role_type(user) == "manager":
        query = query.filter(User.project_id == user.project_id)
    return query


def list_requests(user, status):
    query = _requests_query(user)
    if status != "all":
        query = query.filter(PracticeRequest.status == status)
    return [serialize_request(r, user) for r in query.order_by(PracticeRequest.created_at.desc()).all()]


def create_request(user, data):
    row = PracticeRequest(requested_by_id=user.id, name=data["name"].strip(), tin=clean_tin(data.get("tin")),
                          note=(data.get("note") or "").strip() or None)
    db.session.add(row)
    db.session.commit()
    return row


def resolve_request(user, request_id, data):
    row = db.session.get(PracticeRequest, request_id)
    if row is None or row.status != "pending":
        abort(404, message="Pending request not found.")
    if role_type(user) == "manager" and row.requested_by.project_id != user.project_id:
        abort(404, message="Pending request not found.")
    action = data["action"]
    if action == "reject":
        row.status = "rejected"
    else:
        if action == "assign":
            if not data.get("practice_id"):
                abort(422, message="practiceId is required to assign an existing practice.")
            practice = get_practice(user, data["practice_id"])
            row.status = "assigned"
        else:
            practice = create_practice(user, data.get("practice") or {"name": row.name, "tin": row.tin}, commit=False)
            row.status = "created"
        if PracticeAssignment.query.filter_by(practice_id=practice.id, user_id=row.requested_by_id).first() is None:
            db.session.add(PracticeAssignment(
                practice_id=practice.id, user_id=row.requested_by_id, access_status="Request to Practice Sent",
                access_status_since=business_today(), assigned_by_id=user.id))
        row.practice_id = practice.id
    row.resolved_by_id = user.id
    row.resolved_at = datetime.now(timezone.utc)
    row.resolution_note = (data.get("note") or "").strip() or None
    db.session.commit()
    return row
