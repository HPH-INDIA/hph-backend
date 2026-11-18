"""IT tickets: one record per Jira ticket. See app/it_tickets/access.py for visibility."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from flask_smorest import abort
from sqlalchemy.exc import IntegrityError

from app.cohorts.progression import business_today
from app.extensions import db
from app.it_tickets.access import (
    apply_user_scope, can_moderate_tickets, full_name, is_oversight, person, role_filter, ticket_role,
    visible_user_ids_query,
)
from app.it_tickets.keys import normalize_jira_key
from app.it_tickets.models import IssueCategory, ItTicket, ItTicketEvent, ManualRecordTicket
from app.manual_daily_records.models import ManualDailyRecord
from app.practices.models import Practice, PracticeAssignment
from app.roles.models import Role
from app.users.models import User

OWNER_FIELDS = ("issue_category_id", "hph_status_note", "charts_available", "not_in_kairon", "follow_up_issue",
                "status", "resolved_on", "opened_on")
STANDARD_DAY_HOURS = Decimal("8")
DELETE_REASONS = ("Logged by mistake", "Duplicate of another ticket", "Wrong practice or person", "Other")


# ---- filtering ---------------------------------------------------------------

def filtered_query(user, args):
    query = ItTicket.query.join(User, User.id == ItTicket.user_id).join(Practice, Practice.id == ItTicket.practice_id)
    query = apply_user_scope(query, ItTicket.user_id, user, args.get("scope") or "all")
    query = role_filter(query, ItTicket.user_id, args.get("role"))
    if args.get("user_id"):
        query = query.filter(ItTicket.user_id.in_(args["user_id"]))
    if args.get("status"):
        query = query.filter(ItTicket.status.in_(args["status"]))
    if args.get("access_status"):
        sub = db.select(PracticeAssignment.id).where(
            PracticeAssignment.practice_id == ItTicket.practice_id, PracticeAssignment.user_id == ItTicket.user_id,
            PracticeAssignment.access_status.in_(args["access_status"]))
        query = query.filter(sub.exists())
    if args.get("practice_id"):
        query = query.filter(ItTicket.practice_id == args["practice_id"])
    if args.get("category_id"):
        query = query.filter(ItTicket.issue_category_id == args["category_id"])
    if args.get("opened_from"):
        query = query.filter(ItTicket.opened_on >= args["opened_from"])
    if args.get("opened_to"):
        query = query.filter(ItTicket.opened_on <= args["opened_to"])
    if args.get("search"):
        term = f"%{args['search'].strip().lower()}%"
        query = query.filter(db.or_(db.func.lower(ItTicket.jira_key).like(term), Practice.name_key.like(term)))
    return query


def _ordered(query, sort):
    if sort == "newest":
        return query.order_by(ItTicket.opened_on.desc(), ItTicket.id.desc())
    if sort == "aging":
        return query.order_by(ItTicket.status.asc(), ItTicket.opened_on.asc(), ItTicket.id.asc())
    return query.order_by(ItTicket.opened_on.asc(), ItTicket.id.asc())


# ---- serialisation -----------------------------------------------------------

def aging_days(ticket, today=None):
    end = ticket.resolved_on if ticket.status == "resolved" and ticket.resolved_on else (today or business_today())
    return max((end - ticket.opened_on).days, 0)


def _context(tickets):
    ids = [t.id for t in tickets]
    pairs = {(t.practice_id, t.user_id) for t in tickets}
    hours = dict(db.session.execute(
        db.select(ManualRecordTicket.ticket_id, db.func.sum(ManualRecordTicket.hours))
        .where(ManualRecordTicket.ticket_id.in_(ids)).group_by(ManualRecordTicket.ticket_id)).all()) if ids else {}
    access = {}
    if pairs:
        practice_ids = {p for p, _ in pairs}
        for assignment in PracticeAssignment.query.filter(PracticeAssignment.practice_id.in_(practice_ids)).all():
            access[(assignment.practice_id, assignment.user_id)] = assignment.access_status
    return hours, access


def serialize_ticket(ticket, viewer, hours=None, access=None, today=None):
    if hours is None or access is None:
        hours, access = _context([ticket])
    owner = ticket.user_id == viewer.id
    moderator = can_moderate_tickets(viewer)
    return {
        "id": ticket.id, "jiraKey": ticket.jira_key, "userId": ticket.user_id, "userName": full_name(ticket.user),
        "empId": ticket.user.emp_id, "role": ticket_role(ticket.user), "practiceId": ticket.practice_id,
        "practiceName": ticket.practice.name, "practiceStatus": ticket.practice.status,
        "issueCategoryId": ticket.issue_category_id,
        "issueCategory": ticket.issue_category.name if ticket.issue_category else None,
        "hphStatusNote": ticket.hph_status_note, "openedOn": ticket.opened_on.isoformat(),
        "agingDays": aging_days(ticket, today), "status": ticket.status,
        "resolvedOn": ticket.resolved_on.isoformat() if ticket.resolved_on else None,
        "resolutionNote": ticket.resolution_note, "chartsAvailable": ticket.charts_available,
        "notInKairon": ticket.not_in_kairon, "followUpIssue": ticket.follow_up_issue,
        "accessStatus": access.get((ticket.practice_id, ticket.user_id)),
        "linkedIdleHours": float(hours.get(ticket.id) or 0), "source": ticket.source,
        "updatedAt": ticket.updated_at.isoformat() if ticket.updated_at else None,
        "canEdit": owner, "canChangeStatus": owner or moderator, "canDelete": moderator,
    }


def serialize_many(tickets, viewer):
    hours, access = _context(tickets)
    today = business_today()
    return [serialize_ticket(t, viewer, hours, access, today) for t in tickets]


def serialize_event(event):
    return {"id": event.id, "action": event.action, "actorName": full_name(event.actor) if event.actor else None,
            "changes": event.changes or {}, "reason": event.reason, "createdAt": event.created_at.isoformat()}


# ---- reads -------------------------------------------------------------------

def list_tickets(user, args):
    query = filtered_query(user, args)
    total = query.count()
    page, page_size = args.get("page", 1), args.get("page_size", 25)
    tickets = _ordered(query, args.get("sort")).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": serialize_many(tickets, user), "total": total, "page": page, "pageSize": page_size}


def get_ticket(user, ticket_id):
    query = apply_user_scope(ItTicket.query.join(User, User.id == ItTicket.user_id), ItTicket.user_id, user)
    ticket = query.filter(ItTicket.id == ticket_id).first()
    if ticket is None:
        abort(404, message="Ticket not found.")
    return ticket


def ticket_detail(user, ticket):
    events = (ItTicketEvent.query.filter_by(ticket_id=ticket.id)
              .order_by(ItTicketEvent.created_at.desc(), ItTicketEvent.id.desc()).all())
    return {**serialize_ticket(ticket, user), "events": [serialize_event(e) for e in events]}


def _month_start(today):
    return today.replace(day=1)


def idle_hours_this_month(user_ids):
    if not user_ids:
        return {}
    today = business_today()
    rows = db.session.execute(
        db.select(ManualDailyRecord.user_id, db.func.sum(ManualRecordTicket.hours))
        .join(ManualDailyRecord, ManualDailyRecord.id == ManualRecordTicket.manual_record_id)
        .where(ManualDailyRecord.user_id.in_(user_ids), ManualDailyRecord.record_date >= _month_start(today))
        .group_by(ManualDailyRecord.user_id)).all()
    return {user_id: float(total or 0) for user_id, total in rows}


def is_full_idle(record_like):
    idle = Decimal(record_like.get("tech_issues_downtime_hours") or 0) + Decimal(record_like.get("no_inventory_idle_time_hours") or 0)
    available = STANDARD_DAY_HOURS - Decimal(record_like.get("leave_hours") or 0)
    return available > 0 and idle >= available, idle


def idle_days_without_ticket(user_ids):
    if not user_ids:
        return {}
    today = business_today()
    linked = db.select(ManualRecordTicket.manual_record_id)
    records = (ManualDailyRecord.query.filter(ManualDailyRecord.user_id.in_(user_ids),
                                              ManualDailyRecord.record_date >= _month_start(today),
                                              ManualDailyRecord.id.notin_(linked)).all())
    counts = Counter()
    for record in records:
        full, _ = is_full_idle({"tech_issues_downtime_hours": record.tech_issues_downtime_hours,
                                "no_inventory_idle_time_hours": record.no_inventory_idle_time_hours,
                                "leave_hours": record.leave_hours})
        if full:
            counts[record.user_id] += 1
    return dict(counts)


MAX_IDLE_RANGE_DAYS = 366


def _week_start(day):
    return day - timedelta(days=day.weekday())


def _hours(value):
    return round(float(value), 2)


def idle_time(user, args):
    """Idle hours coders linked to IT tickets in their daily records - the
    productivity lost to tickets - by day or week (weeks start on Monday).

    Only lead-approved records count; hours on records still awaiting review
    are returned separately. Ticket filters (role, person, practice, category,
    status, opened dates, search) narrow which tickets' hours are included.
    """
    today = business_today()
    start = args.get("idle_from") or _month_start(today)
    end = args.get("idle_to") or today
    if start > end:
        abort(422, message="The start date must be on or before the end date.")
    if (end - start).days >= MAX_IDLE_RANGE_DAYS:
        abort(422, message="Choose a range of a year or less.")
    window_start = min(start, _month_start(today), _week_start(today))
    window_end = max(end, today)

    ticket_ids = filtered_query(user, args).with_entities(ItTicket.id)
    rows = db.session.execute(
        db.select(ManualDailyRecord.record_date, ManualDailyRecord.status, ManualDailyRecord.user_id,
                  ManualRecordTicket.ticket_id, ManualRecordTicket.hours, ItTicket.practice_id)
        .join(ManualDailyRecord, ManualDailyRecord.id == ManualRecordTicket.manual_record_id)
        .join(ItTicket, ItTicket.id == ManualRecordTicket.ticket_id)
        .where(ManualRecordTicket.ticket_id.in_(ticket_ids),
               ManualDailyRecord.record_date >= window_start, ManualDailyRecord.record_date <= window_end,
               ManualDailyRecord.status.in_(("approved", "pending")))).all()

    roles = {u.id: ticket_role(u) for u in User.query.options(db.selectinload(User.role).selectinload(Role.role_type))
             .filter(User.id.in_({r.user_id for r in rows})).all()} if rows else {}
    practice_names = dict(db.session.execute(
        db.select(Practice.id, Practice.name).where(Practice.id.in_({r.practice_id for r in rows}))).all()) if rows else {}

    def bucket():
        return {"hours": Decimal(0), "people": set(), "tickets": set(), "days": set(),
                "byRole": {"coder": Decimal(0), "qa": Decimal(0)}, "practices": Counter()}

    def add(target, row):
        target["hours"] += row.hours
        target["people"].add(row.user_id)
        target["tickets"].add(row.ticket_id)
        target["days"].add(row.record_date)
        role = roles.get(row.user_id)
        if role in target["byRole"]:
            target["byRole"][role] += row.hours
        target["practices"][row.practice_id] += row.hours

    def out(target):
        top = target["practices"].most_common(1)
        return {"hours": _hours(target["hours"]), "people": len(target["people"]), "tickets": len(target["tickets"]),
                "days": len(target["days"]), "byRole": {k: _hours(v) for k, v in target["byRole"].items()},
                "topPractice": {"id": top[0][0], "name": practice_names.get(top[0][0]), "hours": _hours(top[0][1])} if top else None}

    totals, this_week, this_month, periods = bucket(), bucket(), bucket(), defaultdict(bucket)
    pending = Decimal(0)
    week_start, month_start = _week_start(today), _month_start(today)
    for row in rows:
        if row.status != "approved":
            if start <= row.record_date <= end:
                pending += row.hours
            continue
        if week_start <= row.record_date <= today:
            add(this_week, row)
        if month_start <= row.record_date <= today:
            add(this_month, row)
        if start <= row.record_date <= end:
            add(totals, row)
            key = row.record_date if args.get("group") != "week" else _week_start(row.record_date)
            add(periods[key], row)

    # Full idle days (approved) with no ticket linked, for the same people.
    people = apply_user_scope(ManualDailyRecord.query, ManualDailyRecord.user_id, user)
    people = role_filter(people, ManualDailyRecord.user_id, args.get("role"))
    if args.get("user_id"):
        people = people.filter(ManualDailyRecord.user_id.in_(args["user_id"]))
    unlinked = people.filter(
        ManualDailyRecord.status == "approved",
        ManualDailyRecord.record_date >= start, ManualDailyRecord.record_date <= end,
        ManualDailyRecord.id.notin_(db.select(ManualRecordTicket.manual_record_id))).all()
    without_ticket = sum(1 for record in unlinked if is_full_idle({
        "tech_issues_downtime_hours": record.tech_issues_downtime_hours,
        "no_inventory_idle_time_hours": record.no_inventory_idle_time_hours,
        "leave_hours": record.leave_hours})[0])

    week = args.get("group") == "week"
    return {
        "fromDate": start.isoformat(), "toDate": end.isoformat(), "group": "week" if week else "day",
        "totals": out(totals), "thisWeek": out(this_week), "thisMonth": out(this_month),
        "pendingHours": _hours(pending), "fullIdleDaysWithoutTicket": without_ticket,
        "rows": [{"periodStart": (max(key, start) if week else key).isoformat(),
                  "periodEnd": (min(key + timedelta(days=6), end) if week else key).isoformat(),
                  **out(value)} for key, value in sorted(periods.items(), reverse=True)],
    }


def summary(user, args):
    tickets = filtered_query(user, args).all()
    today = business_today()
    by_role = {role: {"total": 0, "inProgress": 0, "resolved": 0} for role in ("coder", "qa")}
    open_ages, open_practices = [], set()
    for ticket in tickets:
        role = ticket_role(ticket.user)
        is_open = ticket.status == "in_progress"
        if role in by_role:
            by_role[role]["total"] += 1
            by_role[role]["inProgress" if is_open else "resolved"] += 1
        if is_open:
            open_ages.append(aging_days(ticket, today))
            open_practices.add(ticket.practice_id)
    hours = idle_hours_this_month(sorted({t.user_id for t in tickets}))
    return {
        "total": len(tickets), "inProgress": sum(1 for t in tickets if t.status == "in_progress"),
        "resolved": sum(1 for t in tickets if t.status == "resolved"), "byRole": by_role,
        "practicesWithOpenTickets": len(open_practices),
        "avgOpenAgingDays": round(sum(open_ages) / len(open_ages)) if open_ages else 0,
        "maxOpenAgingDays": max(open_ages) if open_ages else 0,
        "idleHoursLinkedThisMonth": round(sum(hours.values()), 2),
    }


def _top(counter):
    return counter.most_common(1)[0][0] if counter else None


def practice_wise(user, args):
    tickets = _ordered(filtered_query(user, args), "oldest").all()
    serialized = {s["id"]: s for s in serialize_many(tickets, user)}
    groups = defaultdict(list)
    for ticket in tickets:
        groups[ticket.practice_id].append(ticket)
    rows = []
    for practice_id, items in groups.items():
        practice = items[0].practice
        open_items = [t for t in items if t.status == "in_progress"]
        latest_charts = max(items, key=lambda t: t.charts_updated_at or t.created_at)
        rows.append({
            "practiceId": practice_id, "practiceName": practice.name, "practiceStatus": practice.status,
            "peopleCount": len({t.user_id for t in items}), "ticketCount": len(items),
            "openCount": len(open_items), "resolvedCount": len(items) - len(open_items),
            "topReason": _top(Counter(t.issue_category.name for t in items if t.issue_category)),
            "oldestOpenDays": max((serialized[t.id]["agingDays"] for t in open_items), default=None),
            "chartsAvailable": "Not in Kairon" if latest_charts.not_in_kairon else latest_charts.charts_available,
            "chartsUpdatedAt": (latest_charts.charts_updated_at or latest_charts.created_at).isoformat(),
            "rolesBlocked": sorted({ticket_role(t.user) for t in open_items if ticket_role(t.user)}),
            "tickets": [serialized[t.id] for t in items],
        })
    rows.sort(key=lambda r: (-(r["oldestOpenDays"] or -1), r["practiceName"].lower()))
    return rows


def coder_wise(user, args):
    tickets = _ordered(filtered_query(user, args), "oldest").all()
    serialized = {s["id"]: s for s in serialize_many(tickets, user)}
    groups = defaultdict(list)
    for ticket in tickets:
        groups[ticket.user_id].append(ticket)
    user_ids = sorted(groups)
    hours, missing = idle_hours_this_month(user_ids), idle_days_without_ticket(user_ids)
    rows = []
    for user_id, items in groups.items():
        owner = items[0].user
        open_items = [t for t in items if t.status == "in_progress"]
        rows.append({
            "userId": user_id, "userName": full_name(owner), "empId": owner.emp_id, "role": ticket_role(owner),
            "practicesBlocked": len({t.practice_id for t in open_items}), "openCount": len(open_items),
            "resolvedCount": len(items) - len(open_items),
            "topReason": _top(Counter(t.issue_category.name for t in items if t.issue_category)),
            "oldestOpenDays": max((serialized[t.id]["agingDays"] for t in open_items), default=None),
            "idleHoursLinkedThisMonth": hours.get(user_id, 0), "idleDaysWithoutTicket": missing.get(user_id, 0),
            "tickets": [serialized[t.id] for t in items],
        })
    rows.sort(key=lambda r: (-(r["oldestOpenDays"] or -1), r["userName"].lower()))
    return rows


def access_matrix(user, args):
    visible = visible_user_ids_query(user)
    assignments = PracticeAssignment.query.join(Practice, Practice.id == PracticeAssignment.practice_id).filter(
        Practice.status != "Terminated")
    if visible is not None:
        assignments = assignments.filter(PracticeAssignment.user_id.in_(visible))
    assignments = role_filter(assignments, PracticeAssignment.user_id, args.get("role"))
    if args.get("user_id"):
        assignments = assignments.filter(PracticeAssignment.user_id.in_(args["user_id"]))
    if args.get("practice_id"):
        assignments = assignments.filter(PracticeAssignment.practice_id == args["practice_id"])
    open_keys = defaultdict(list)
    for ticket in filtered_query(user, {**args, "status": ["in_progress"]}).all():
        open_keys[(ticket.practice_id, ticket_role(ticket.user))].append(ticket.jira_key)
    rows = {}
    for assignment in assignments.all():
        role = ticket_role(assignment.user)
        if role is None:
            continue
        row = rows.setdefault(assignment.practice_id, {
            "practiceId": assignment.practice_id, "practiceName": assignment.practice.name,
            "coder": {"people": [], "blocked": False, "jiraKeys": []}, "qa": {"people": [], "blocked": False, "jiraKeys": []}})
        row[role]["people"].append(full_name(assignment.user))
    for (practice_id, role), keys in open_keys.items():
        if practice_id in rows and role in ("coder", "qa"):
            rows[practice_id][role]["blocked"] = True
            rows[practice_id][role]["jiraKeys"] = sorted(set(keys))
    counts = {"bothOk": 0, "coderOkQaBlocked": 0, "coderBlockedQaOk": 0, "bothBlocked": 0}
    for row in rows.values():
        c, q = row["coder"]["blocked"], row["qa"]["blocked"]
        counts["bothBlocked" if c and q else "coderBlockedQaOk" if c else "coderOkQaBlocked" if q else "bothOk"] += 1
    ordered = sorted(rows.values(), key=lambda r: (not (r["coder"]["blocked"] or r["qa"]["blocked"]), r["practiceName"].lower()))
    return {"rows": ordered, "counts": counts}


def filter_options(user):
    people_query = User.query.filter(User.is_active.is_(True))
    visible = visible_user_ids_query(user)
    if visible is not None:
        people_query = people_query.filter(User.id.in_(visible))
    people = [p for p in (person(u) for u in people_query.order_by(User.first_name, User.last_name).all()) if p["role"]]
    practices = Practice.query
    if not is_oversight(user):
        practices = practices.filter(Practice.id.in_(db.select(PracticeAssignment.practice_id).where(PracticeAssignment.user_id == user.id)))
    elif visible is not None:
        practices = practices.filter(Practice.project_id == user.project_id)
    return {
        "people": people,
        "practices": [{"id": p.id, "name": p.name} for p in practices.order_by(Practice.name_key).all()],
        "categories": list_categories(include_retired=False),
    }


# ---- writes ------------------------------------------------------------------

def _event(ticket, action, actor, changes=None, reason=None):
    db.session.add(ItTicketEvent(ticket_id=ticket.id, jira_key=ticket.jira_key, action=action,
                                 actor_id=actor.id if actor else None, changes=changes or {}, reason=reason))


def _category(category_id):
    if category_id is None:
        return None
    category = db.session.get(IssueCategory, category_id)
    if category is None or not category.active:
        abort(422, message="Choose an active issue category.")
    return category


def _clean_text(value):
    if isinstance(value, str):
        value = value.strip()
    return value or None


def create_ticket(user, data):
    key = normalize_jira_key(data["jira_key"])
    if key is None:
        abort(422, message="Jira ticket ID must look like VTEC-12345.")
    assignment = PracticeAssignment.query.filter_by(practice_id=data["practice_id"], user_id=user.id).first()
    practice = db.session.get(Practice, data["practice_id"])
    if assignment is None or practice is None:
        abort(422, message="You can only log tickets for practices assigned to you.")
    if practice.status == "Terminated":
        abort(422, message="This practice is terminated. New tickets can't be logged for it.")
    if ItTicket.query.filter_by(jira_key=key).first() is not None:
        abort(409, message=f"{key} is already logged in HPH.")
    _category(data["issue_category_id"])
    opened_on = data.get("opened_on") or business_today()
    if opened_on > business_today():
        abort(422, message="Ticket open date can't be in the future.")
    ticket = ItTicket(
        jira_key=key, user_id=user.id, practice_id=practice.id, issue_category_id=data["issue_category_id"],
        hph_status_note=_clean_text(data.get("hph_status_note")), opened_on=opened_on,
        charts_available=None if data.get("not_in_kairon") else data.get("charts_available"),
        not_in_kairon=bool(data.get("not_in_kairon")), charts_updated_at=datetime.now(timezone.utc),
        source="manual", created_by_id=user.id)
    db.session.add(ticket)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        abort(409, message=f"{key} is already logged in HPH.")
    assignment.access_status = "IT Ticket Received"
    assignment.access_status_since = business_today()
    _event(ticket, "created", user, {"jiraKey": key, "practice": practice.name})
    db.session.commit()
    return ticket


def _apply_status(ticket, status, resolved_on):
    if status == "resolved":
        ticket.status = "resolved"
        ticket.resolved_on = resolved_on or business_today()
        if ticket.resolved_on < ticket.opened_on:
            abort(422, message="Resolved date can't be before the open date.")
    else:
        ticket.status = "in_progress"
        ticket.resolved_on = None
        ticket.resolution_note = None


def update_ticket(user, ticket, data):
    owner = ticket.user_id == user.id
    if not owner:
        if not can_moderate_tickets(user):
            abort(403, message="You can only change your own tickets.")
        extra = set(data) - {"status", "resolved_on"}
        if extra or "status" not in data:
            abort(403, message="Managers and admins can change only a ticket's status.")
    before = {"status": ticket.status, "resolvedOn": ticket.resolved_on.isoformat() if ticket.resolved_on else None,
              "chartsAvailable": ticket.charts_available, "notInKairon": ticket.not_in_kairon,
              "issueCategoryId": ticket.issue_category_id, "hphStatusNote": ticket.hph_status_note,
              "followUpIssue": ticket.follow_up_issue, "openedOn": ticket.opened_on.isoformat()}
    if owner:
        if "issue_category_id" in data:
            _category(data["issue_category_id"])
            ticket.issue_category_id = data["issue_category_id"]
        for field in ("hph_status_note", "follow_up_issue"):
            if field in data:
                setattr(ticket, field, _clean_text(data[field]))
        if "opened_on" in data and data["opened_on"]:
            ticket.opened_on = data["opened_on"]
        if "charts_available" in data or "not_in_kairon" in data:
            ticket.not_in_kairon = bool(data.get("not_in_kairon", ticket.not_in_kairon))
            ticket.charts_available = None if ticket.not_in_kairon else data.get("charts_available", ticket.charts_available)
            ticket.charts_updated_at = datetime.now(timezone.utc)
    if "status" in data:
        _apply_status(ticket, data["status"], data.get("resolved_on"))
    elif "resolved_on" in data and ticket.status == "resolved" and data["resolved_on"]:
        ticket.resolved_on = data["resolved_on"]
    after = {"status": ticket.status, "resolvedOn": ticket.resolved_on.isoformat() if ticket.resolved_on else None,
             "chartsAvailable": ticket.charts_available, "notInKairon": ticket.not_in_kairon,
             "issueCategoryId": ticket.issue_category_id, "hphStatusNote": ticket.hph_status_note,
             "followUpIssue": ticket.follow_up_issue, "openedOn": ticket.opened_on.isoformat()}
    changes = {k: [before[k], after[k]] for k in before if before[k] != after[k]}
    if changes:
        _event(ticket, "status_changed" if set(changes) <= {"status", "resolvedOn"} else "updated", user, changes)
    db.session.commit()
    return ticket


def delete_ticket(user, ticket, reason, note=None):
    if not can_moderate_tickets(user):
        abort(403, message="Only managers and admins can delete tickets.")
    snapshot = {"jiraKey": ticket.jira_key, "userId": ticket.user_id, "practiceId": ticket.practice_id,
                "status": ticket.status, "openedOn": ticket.opened_on.isoformat()}
    _event(ticket, "deleted", user, snapshot, reason=f"{reason}: {note}" if note else reason)
    db.session.delete(ticket)
    db.session.commit()


# ---- categories --------------------------------------------------------------

def list_categories(include_retired=False):
    counts = dict(db.session.execute(
        db.select(ItTicket.issue_category_id, db.func.count()).group_by(ItTicket.issue_category_id)).all())
    query = IssueCategory.query
    if not include_retired:
        query = query.filter(IssueCategory.active.is_(True))
    return [{"id": c.id, "name": c.name, "active": c.active, "ticketCount": counts.get(c.id, 0)}
            for c in query.order_by(IssueCategory.name).all()]


def save_category(category, data):
    if "name" in data:
        name = " ".join(data["name"].split())
        clash = IssueCategory.query.filter(db.func.lower(IssueCategory.name) == name.lower())
        if category.id:
            clash = clash.filter(IssueCategory.id != category.id)
        if clash.first() is not None:
            abort(409, message="An issue category with that name already exists.")
        category.name = name
    if "active" in data:
        category.active = data["active"]
    db.session.add(category)
    db.session.commit()
    return {"id": category.id, "name": category.name, "active": category.active,
            "ticketCount": ItTicket.query.filter_by(issue_category_id=category.id).count()}


# ---- manual daily records ----------------------------------------------------

def link_record_tickets(record, owner, tickets, *, enforce=True):
    """Attach the IT tickets behind a manual record's idle hours.

    Rule (Coder (1LR) / QA (2LR) with the it_tickets feature): when idle +
    downtime >= 8 - leave (and that is > 0) at least one of the owner's open
    tickets must be linked and the linked hours must equal the idle hours.
    Partial idle days may link tickets; their hours must not exceed idle.
    `tickets` None means "not sent" - existing links are kept and re-checked.
    """
    from app.auth import has_feature

    if tickets is None and not enforce:
        return
    if ticket_role(owner) is None or not has_feature(owner, "it_tickets", "read"):
        return
    full, idle = is_full_idle({"tech_issues_downtime_hours": record.tech_issues_downtime_hours,
                               "no_inventory_idle_time_hours": record.no_inventory_idle_time_hours,
                               "leave_hours": record.leave_hours})
    existing = {link.ticket_id: link for link in (getattr(record, "ticket_links", None) or [])}
    if tickets is None:
        tickets = [{"ticket_id": tid, "hours": link.hours} for tid, link in existing.items()]
    if full and not tickets and enforce:
        abort(422, message=f"Full idle day ({idle} h): link at least one IT ticket that blocked you and split the hours.")
    if not tickets and not existing:
        return
    seen, total = set(), Decimal("0")
    for item in tickets:
        ticket_id, hours = item["ticket_id"], Decimal(str(item["hours"]))
        if ticket_id in seen:
            abort(422, message="Each ticket can be linked only once per day.")
        seen.add(ticket_id)
        ticket = db.session.get(ItTicket, ticket_id)
        if ticket is None or ticket.user_id != owner.id:
            abort(422, message="You can only link your own IT tickets.")
        if ticket.status != "in_progress" and ticket_id not in existing:
            abort(422, message=f"{ticket.jira_key} is resolved; link only open tickets.")
        total += hours
    if full and enforce and abs(total - idle) > Decimal("0.01"):
        abort(422, message=f"Linked ticket hours ({total} h) must add up to the idle hours ({idle} h).")
    if not full and total > idle + Decimal("0.01"):
        abort(422, message=f"Linked ticket hours ({total} h) can't exceed the idle hours ({idle} h).")
    wanted = {item["ticket_id"]: Decimal(str(item["hours"])) for item in tickets}
    for ticket_id, link in list(existing.items()):
        if ticket_id not in wanted:
            db.session.delete(link)
            record.ticket_links.remove(link)
    for ticket_id, hours in wanted.items():
        if ticket_id in existing:
            existing[ticket_id].hours = hours
        else:
            db.session.add(ManualRecordTicket(manual_record_id=record.id, ticket_id=ticket_id, hours=hours))
