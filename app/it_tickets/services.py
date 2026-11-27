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
from app.it_tickets.models import NO_TICKET_SOURCE, IssueCategory, ItTicket, ItTicketEvent, ManualRecordTicket
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
    # No ticket - idle entries are not tickets: they never appear in ticket lists, counts or aging.
    query = (ItTicket.query.join(User, User.id == ItTicket.user_id).join(Practice, Practice.id == ItTicket.practice_id)
             .filter(ItTicket.source != NO_TICKET_SOURCE))
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
    # Days (daily records) each ticket was ticked as a reason for idle time.
    days = dict(db.session.execute(
        db.select(ManualRecordTicket.ticket_id, db.func.count(ManualRecordTicket.manual_record_id))
        .where(ManualRecordTicket.ticket_id.in_(ids)).group_by(ManualRecordTicket.ticket_id)).all()) if ids else {}
    access = {}
    if pairs:
        practice_ids = {p for p, _ in pairs}
        for assignment in PracticeAssignment.query.filter(PracticeAssignment.practice_id.in_(practice_ids)).all():
            access[(assignment.practice_id, assignment.user_id)] = assignment.access_status
    return days, access


def serialize_ticket(ticket, viewer, days=None, access=None, today=None):
    if days is None or access is None:
        days, access = _context([ticket])
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
        "linkedIdleDays": int(days.get(ticket.id) or 0), "source": ticket.source,
        "updatedAt": ticket.updated_at.isoformat() if ticket.updated_at else None,
        "canEdit": owner, "canChangeStatus": owner or moderator, "canDelete": moderator,
    }


def serialize_many(tickets, viewer):
    days, access = _context(tickets)
    today = business_today()
    return [serialize_ticket(t, viewer, days, access, today) for t in tickets]


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
    ticket = query.filter(ItTicket.id == ticket_id, ItTicket.source != NO_TICKET_SOURCE).first()
    if ticket is None:
        abort(404, message="Ticket not found.")
    return ticket


def ticket_detail(user, ticket):
    events = (ItTicketEvent.query.filter_by(ticket_id=ticket.id)
              .order_by(ItTicketEvent.created_at.desc(), ItTicketEvent.id.desc()).all())
    return {**serialize_ticket(ticket, user), "events": [serialize_event(e) for e in events]}


def _month_start(today):
    return today.replace(day=1)


def record_idle_hours():
    """SQL expression: a daily record's idle hours (tech downtime + no-inventory idle)."""
    return (db.func.coalesce(ManualDailyRecord.tech_issues_downtime_hours, 0)
            + db.func.coalesce(ManualDailyRecord.no_inventory_idle_time_hours, 0))


def _records_with_real_ticket():
    """Ids of daily records that have at least one real IT ticket ticked."""
    return (db.select(ManualRecordTicket.manual_record_id)
            .join(ItTicket, ItTicket.id == ManualRecordTicket.ticket_id)
            .where(ItTicket.source != NO_TICKET_SOURCE))


def idle_hours_this_month(user_ids):
    """Per person: this month's idle hours on days they ticked at least one IT
    ticket. Each day counts once, from the daily record - never split by ticket."""
    if not user_ids:
        return {}
    today = business_today()
    rows = db.session.execute(
        db.select(ManualDailyRecord.user_id, db.func.sum(record_idle_hours()))
        .where(ManualDailyRecord.user_id.in_(user_ids), ManualDailyRecord.record_date >= _month_start(today),
               ManualDailyRecord.id.in_(_records_with_real_ticket()))
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


TICKET_ONLY_FILTERS = ("status", "access_status", "practice_id", "category_id", "opened_from", "opened_to", "search")


def _idle_days(user, args, start, end):
    """Every daily record with idle hours in [start, end] for the people the
    viewer may see (role / person filters applied): one entry per person per
    day, approved or pending. Idle hours always come from the record itself -
    the IT tickets ticked on it are reasons, never a split of the hours."""
    people = apply_user_scope(db.select(ManualDailyRecord.id), ManualDailyRecord.user_id, user)
    people = role_filter(people, ManualDailyRecord.user_id, args.get("role"))
    if args.get("user_id"):
        people = people.where(ManualDailyRecord.user_id.in_(args["user_id"]))
    idle = record_idle_hours()
    records = db.session.execute(
        db.select(ManualDailyRecord.id, ManualDailyRecord.user_id, ManualDailyRecord.record_date,
                  ManualDailyRecord.status, ManualDailyRecord.leave_hours, idle.label("idle"))
        .where(ManualDailyRecord.id.in_(people), ManualDailyRecord.record_date >= start,
               ManualDailyRecord.record_date <= end, ManualDailyRecord.status.in_(("approved", "pending")),
               idle > 0)
        .order_by(ManualDailyRecord.record_date.desc(), ManualDailyRecord.user_id.asc())).all()
    links = defaultdict(list)
    ids = [r.id for r in records]
    if ids:
        for link in (ManualRecordTicket.query
                     .options(db.joinedload(ManualRecordTicket.ticket).joinedload(ItTicket.practice))
                     .filter(ManualRecordTicket.manual_record_id.in_(ids)).all()):
            links[link.manual_record_id].append(link.ticket)
    return [{"id": r.id, "user_id": r.user_id, "date": r.record_date, "status": r.status,
             "leave": Decimal(r.leave_hours or 0), "idle": Decimal(r.idle),
             "tickets": sorted(links[r.id], key=lambda t: t.jira_key)} for r in records]


def _is_full_idle_day(day):
    available = STANDARD_DAY_HOURS - day["leave"]
    return available > 0 and day["idle"] >= available


def _ticket_ids(user, args):
    return {row[0] for row in filtered_query(user, args).with_entities(ItTicket.id).all()}


def idle_time(user, args):
    """Idle hours lost to IT tickets, by day or week (weeks start on Monday).

    Each person's day counts once with the idle hours on their daily record
    (tech downtime + no-inventory idle), however many tickets they ticked.
    A day counts as ticket idle when it has at least one ticket matching the
    filters, and as No ticket - idle when it is marked that way. Only
    lead-approved records count; hours awaiting review come back as pendingHours.
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

    wanted = _ticket_ids(user, args)
    days = _idle_days(user, args, window_start, window_end)
    roles = {u.id: ticket_role(u) for u in User.query.options(db.selectinload(User.role).selectinload(Role.role_type))
             .filter(User.id.in_({d["user_id"] for d in days})).all()} if days else {}
    practice_names = {}

    def bucket():
        return {"hours": Decimal(0), "people": set(), "tickets": set(), "days": set(),
                "byRole": {"coder": Decimal(0), "qa": Decimal(0)}, "practices": Counter(),
                "noTicketHours": Decimal(0), "noTicketPeople": set(), "noTicketDays": 0}

    def add(target, day, tickets):
        target["hours"] += day["idle"]
        target["people"].add(day["user_id"])
        target["tickets"].update(t.id for t in tickets)
        target["days"].add(day["date"])
        role = roles.get(day["user_id"])
        if role in target["byRole"]:
            target["byRole"][role] += day["idle"]
        for practice_id in dict.fromkeys(t.practice_id for t in tickets):  # ordered, each practice once
            target["practices"][practice_id] += 1

    def add_no_ticket(target, day, _tickets):
        target["noTicketHours"] += day["idle"]
        target["noTicketPeople"].add(day["user_id"])
        target["noTicketDays"] += 1

    def out(target):
        top = target["practices"].most_common(1)
        return {"hours": _hours(target["hours"]), "people": len(target["people"]), "tickets": len(target["tickets"]),
                "days": len(target["days"]), "byRole": {k: _hours(v) for k, v in target["byRole"].items()},
                "noTicketHours": _hours(target["noTicketHours"]), "noTicketPeople": len(target["noTicketPeople"]),
                "noTicketDays": target["noTicketDays"],
                "topPractice": {"id": top[0][0], "name": practice_names.get(top[0][0]), "days": top[0][1]} if top else None}

    totals, this_week, this_month, periods = bucket(), bucket(), bucket(), defaultdict(bucket)
    pending = Decimal(0)
    without_ticket = 0
    week_start, month_start = _week_start(today), _month_start(today)
    for day in days:
        tickets = [t for t in day["tickets"] if t.id in wanted]
        if tickets:
            adder = add
            practice_names.update({t.practice_id: t.practice.name for t in tickets})
        elif any(t.source == NO_TICKET_SOURCE for t in day["tickets"]):
            adder = add_no_ticket
        else:
            if (not day["tickets"] and day["status"] == "approved" and start <= day["date"] <= end
                    and _is_full_idle_day(day)):
                without_ticket += 1
            continue
        if day["status"] != "approved":
            if start <= day["date"] <= end:
                pending += day["idle"]
            continue
        if week_start <= day["date"] <= today:
            adder(this_week, day, tickets)
        if month_start <= day["date"] <= today:
            adder(this_month, day, tickets)
        if start <= day["date"] <= end:
            adder(totals, day, tickets)
            key = day["date"] if args.get("group") != "week" else _week_start(day["date"])
            adder(periods[key], day, tickets)

    week = args.get("group") == "week"
    return {
        "fromDate": start.isoformat(), "toDate": end.isoformat(), "group": "week" if week else "day",
        "totals": out(totals), "thisWeek": out(this_week), "thisMonth": out(this_month),
        "pendingHours": _hours(pending), "fullIdleDaysWithoutTicket": without_ticket,
        "rows": [{"periodStart": (max(key, start) if week else key).isoformat(),
                  "periodEnd": (min(key + timedelta(days=6), end) if week else key).isoformat(),
                  **out(value)} for key, value in sorted(periods.items(), reverse=True)],
    }


def idle_period(period, day):
    """(first, last) day of the day, Monday-start week or calendar month containing `day`."""
    if period == "week":
        start = _week_start(day)
        return start, start + timedelta(days=6)
    if period == "month":
        start = _month_start(day)
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return start, next_month - timedelta(days=1)
    return day, day


def _ticket_tag(ticket):
    return {"id": ticket.id, "jiraKey": ticket.jira_key, "practiceId": ticket.practice_id,
            "practiceName": ticket.practice.name, "status": ticket.status}


def idle_by_coder(user, args):
    """Idle time for a day / week / month as ONE row per person per day: the idle
    hours on that day's lead-approved daily record, with the IT tickets they
    ticked listed as reasons (no per-ticket hours). Ticket filters keep only
    days with a matching ticket; without them, days with no ticket (or marked
    No ticket - idle) are included too."""
    period = args.get("period") or "day"
    start, end = idle_period(period, args.get("on") or business_today())
    ticket_filtered = any(args.get(key) for key in TICKET_ONLY_FILTERS)
    wanted = _ticket_ids(user, args)
    days = _idle_days(user, args, start, end)

    owners = {u.id: u for u in User.query.options(db.selectinload(User.role).selectinload(Role.role_type))
              .filter(User.id.in_({d["user_id"] for d in days})).all()} if days else {}
    rows, pending = [], Decimal(0)
    ticket_hours, no_ticket_hours, ticket_ids = Decimal(0), Decimal(0), set()
    for day in days:
        real = [t for t in day["tickets"] if t.source != NO_TICKET_SOURCE]
        matching = [t for t in real if t.id in wanted]
        if ticket_filtered and not matching:
            continue
        if day["status"] != "approved":
            pending += day["idle"]
            continue
        owner = owners[day["user_id"]]
        if real:
            ticket_hours += day["idle"]
            ticket_ids.update(t.id for t in real)
        else:
            no_ticket_hours += day["idle"]
        rows.append({
            "recordId": day["id"], "userId": owner.id, "userName": full_name(owner), "empId": owner.emp_id,
            "role": ticket_role(owner), "date": day["date"].isoformat(), "idleHours": _hours(day["idle"]),
            "fullDay": _is_full_idle_day(day), "tickets": [_ticket_tag(t) for t in real],
            "noTicket": not real and any(t.source == NO_TICKET_SOURCE for t in day["tickets"]),
        })
    rows.sort(key=lambda r: (r["userName"].lower(), r["date"]))
    return {
        "period": period, "fromDate": start.isoformat(), "toDate": end.isoformat(),
        "rows": rows, "pendingHours": _hours(pending),
        "totals": {"idleHours": _hours(ticket_hours + no_ticket_hours), "ticketHours": _hours(ticket_hours),
                   "noTicketHours": _hours(no_ticket_hours), "people": len({r["userId"] for r in rows}),
                   "days": len(rows), "tickets": len(ticket_ids)},
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
    practices = Practice.query.filter(Practice.is_system.is_(False))
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

SYSTEM_PRACTICE_NAME = "All practices (no ticket)"
NO_TICKET_NOTE = "Idle all day with no IT ticket (no inventory or another reason)."


def system_practice():
    """The hidden practice No ticket - idle entries use (created by migration; recreated if missing)."""
    practice = Practice.query.filter(Practice.is_system.is_(True)).order_by(Practice.id.asc()).first()
    if practice is None:
        practice = Practice(name=SYSTEM_PRACTICE_NAME, name_key=SYSTEM_PRACTICE_NAME.lower(), status="Active", is_system=True)
        db.session.add(practice)
        db.session.flush()
    return practice


def no_ticket_key(owner, day):
    """NOTICKET-YYYYMMDD-<employee id, else user id>; at most 32 characters."""
    ident = "".join(ch for ch in (owner.emp_id or "") if ch.isalnum() or ch in "_-") or str(owner.id)
    return f"NOTICKET-{day:%Y%m%d}-{ident}"[:32]


def _no_ticket_entry(owner, day):
    """One generic entry per person per day, created resolved on that day."""
    key = no_ticket_key(owner, day)
    entry = ItTicket.query.filter_by(jira_key=key).first()
    if entry is None:
        entry = ItTicket(jira_key=key, user_id=owner.id, practice_id=system_practice().id, source=NO_TICKET_SOURCE,
                         status="resolved", opened_on=day, resolved_on=day, hph_status_note=NO_TICKET_NOTE,
                         created_by_id=owner.id)
        db.session.add(entry)
        db.session.flush()
    return entry


def remove_unused_no_ticket_entries(user_id, day):
    """Drops that day's No ticket - idle entry once no daily record links it."""
    linked = db.select(ManualRecordTicket.ticket_id)
    for entry in ItTicket.query.filter(ItTicket.user_id == user_id, ItTicket.opened_on == day,
                                       ItTicket.source == NO_TICKET_SOURCE, ItTicket.id.notin_(linked)).all():
        db.session.delete(entry)


def link_record_tickets(record, owner, tickets, *, enforce=True, no_ticket=False):
    """Tick the IT tickets behind a manual record's idle hours.

    One daily record holds the day's idle hours (tech downtime + no-inventory
    idle); tickets are only the reasons - their hours are never split.
    Rule (Coder (1LR) / QA (2LR) with the it_tickets feature): when idle +
    downtime >= 8 - leave (and that is > 0) at least one of the owner's open
    tickets must be ticked, or No ticket - idle. Partial idle days may tick
    tickets. A day with no idle hours keeps no tickets.
    `tickets` None means "not sent" - existing links are kept and re-checked.
    Each item is {"ticket_id": int}; an "hours" key from older clients is ignored.
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
    if no_ticket:
        if not full:
            abort(422, message="No ticket - idle is only for a full idle day; tick tickets or leave them out on a partial idle day.")
        if tickets:
            abort(422, message="Choose either IT tickets or No ticket - idle, not both.")
        tickets = [{"ticket_id": _no_ticket_entry(owner, record.record_date).id}]
    if idle <= 0:
        if tickets:
            abort(422, message="Tick IT tickets only on a day with idle or downtime hours.")
        tickets = []
    if tickets is None:
        tickets = [{"ticket_id": tid} for tid in existing]
    if full and not tickets and enforce:
        abort(422, message=f"Full idle day ({idle} h): tick at least one IT ticket that blocked you, or No ticket - idle.")
    if not tickets and not existing:
        return
    wanted = []
    for item in tickets:
        ticket_id = item["ticket_id"]
        if ticket_id in wanted:
            abort(422, message="Each ticket can be ticked only once per day.")
        wanted.append(ticket_id)
        ticket = db.session.get(ItTicket, ticket_id)
        if ticket is None or ticket.user_id != owner.id:
            abort(422, message="You can only link your own IT tickets.")
        if ticket.source == NO_TICKET_SOURCE:
            if ticket.opened_on != record.record_date or not (no_ticket or ticket_id in existing):
                abort(422, message="Use No ticket - idle instead of linking that entry.")
        elif ticket.status != "in_progress" and ticket_id not in existing:
            abort(422, message=f"{ticket.jira_key} is resolved; link only open tickets.")
    if any(db.session.get(ItTicket, tid).source == NO_TICKET_SOURCE for tid in wanted) and len(wanted) > 1:
        abort(422, message="Choose either IT tickets or No ticket - idle, not both.")
    for ticket_id, link in list(existing.items()):
        if ticket_id not in wanted:
            db.session.delete(link)
            record.ticket_links.remove(link)
    for ticket_id in wanted:
        if ticket_id not in existing:
            db.session.add(ManualRecordTicket(manual_record_id=record.id, ticket_id=ticket_id))
    db.session.flush()
    remove_unused_no_ticket_entries(owner.id, record.record_date)
