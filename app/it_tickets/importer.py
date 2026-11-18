"""IT tickets workbook import (IT_Tickets_Tracker.xlsx layout): preview, then commit.

One ticket per Jira ID - a cell with several IDs becomes several tickets,
each with the row's status. Rows match existing tickets on the Jira key.
Statuses are copied as given: nothing is resolved or deleted automatically.
"""
import difflib

from app.cohorts.progression import business_today
from app.extensions import db
from app.it_tickets.keys import is_resolved_status, parse_charts_cell, parse_import_date, split_jira_keys
from app.it_tickets.models import IssueCategory, ItTicket, ItTicketEvent
from app.it_tickets.names import NameResolver, name_key
from app.practices.models import Practice, PracticeAssignment, practice_loose_key, practice_name_key
from app.practices.services import (
    build_practice, created_status_change, default_project_id, link_all_kairon_records, scoped_practices,
)


def _text(value):
    text = str(value).strip() if value is not None else ""
    return text or None


class _Categories:
    def __init__(self):
        self.items = {" ".join(c.name.lower().split()): c for c in IssueCategory.query.all()}
        self.other = self.items.get("other")

    def match(self, raw):
        key = " ".join(str(raw or "").lower().replace("credetials", "credentials").split())
        if not key or key == "resolved":
            return None
        if key in self.items:
            return self.items[key]
        close = difflib.get_close_matches(key, self.items.keys(), n=1, cutoff=0.75)
        return self.items[close[0]] if close else self.other


def _analyse(actor, rows, mappings):
    resolver = NameResolver(actor, mappings)
    practices, loose = {}, {}
    for practice in scoped_practices(actor).order_by(Practice.id.asc()).all():
        practices.setdefault(practice.name_key, practice)
        loose.setdefault(practice_loose_key(practice.name), []).append(practice)
    existing = {t.jira_key: t for t in ItTicket.query.filter(
        ItTicket.jira_key.in_([k for row in rows for k in split_jira_keys(row.get("ticket_id"))])).all()}
    analysed, unmatched_names, unmatched_practices, similar = [], [], [], []
    for index, row in enumerate(rows, start=1):
        keys = split_jira_keys(row.get("ticket_id"))
        coder = _text(row.get("coder_name"))
        practice_name = _text(row.get("practice_name"))
        user = resolver.resolve(coder) if coder else None
        practice = practices.get(practice_name_key(practice_name)) if practice_name else None
        if practice is None and practice_name:
            # Only a single existing practice with the same loose name counts as a match.
            candidates = loose.get(practice_loose_key(practice_name), [])
            if len(candidates) == 1:
                practice = candidates[0]
                pair = {"nameInFile": " ".join(practice_name.split()), "practiceId": practice.id, "practiceName": practice.name}
                if pair not in similar:
                    similar.append(pair)
        errors = []
        if not keys:
            errors.append("No valid VTEC ticket ID.")
        if not coder:
            errors.append("Coder name is missing.")
        if not practice_name:
            errors.append("Practice name is missing.")
        if coder and user is None and coder.strip() not in unmatched_names:
            unmatched_names.append(" ".join(coder.split()))
        if practice_name and practice is None and practice_name not in unmatched_practices:
            unmatched_practices.append(practice_name)
        opened = parse_import_date(row.get("ticket_open_date"))
        if keys and opened is None:
            errors.append("Ticket open date is missing or unreadable.")
        for key in keys or [None]:
            analysed.append({
                "rowNumber": index, "jiraKey": key, "rawJira": _text(row.get("ticket_id")),
                "action": "error" if errors else ("update" if key in existing else "create"),
                "coderName": coder, "userId": user.id if user else None,
                "practiceName": practice_name, "practiceId": practice.id if practice else None,
                "errors": list(errors), "_row": row, "_user": user, "_practice": practice, "_opened": opened,
            })
    return analysed, unmatched_names, unmatched_practices, similar, existing


def preview_ticket_import(actor, rows, mappings):
    analysed, names, practices, similar, _ = _analyse(actor, rows, mappings)
    summary = {"create": 0, "update": 0, "error": 0}
    for item in analysed:
        summary[item["action"]] += 1
    return {"rows": [{k: v for k, v in item.items() if not k.startswith("_")} for item in analysed],
            "unmatchedNames": names, "unmatchedPractices": practices, "similarPractices": similar, "summary": summary}


def commit_ticket_import(actor, rows, mappings):
    """One transaction, and a fixed number of queries however long the file is:
    new practices are inserted together, then tickets, then assignments and
    history events, each in one flush."""
    analysed, _, _, _, existing = _analyse(actor, rows, mappings)
    categories = _Categories()
    result = {"created": 0, "updated": 0, "practicesCreated": 0, "skipped": 0}
    today = business_today()
    kept = [item for item in analysed if item["action"] != "error" and item["_user"] is not None]
    result["skipped"] = len(analysed) - len(kept)

    with db.session.no_autoflush:
        fallback_project_id = default_project_id(actor)
        new_practices = {}
        for item in kept:
            if item["_practice"] is not None:
                continue
            key = practice_name_key(item["practiceName"])
            practice = new_practices.get(key)
            if practice is None:
                project_id = fallback_project_id or item["_user"].project_id
                practice = build_practice(actor, {"name": item["practiceName"]}, project_id=project_id)
                db.session.add(practice)
                new_practices[key] = practice
                result["practicesCreated"] += 1
            item["_practice"] = practice
    db.session.flush()
    db.session.add_all([created_status_change(actor, practice) for practice in new_practices.values()])

    tickets = []
    with db.session.no_autoflush:
        for item in kept:
            row, user, practice = item["_row"], item["_user"], item["_practice"]
            resolved = is_resolved_status(row.get("ticket_status"))
            opened = item["_opened"]
            resolved_on = parse_import_date(row.get("ticket_resolved_date")) if resolved else None
            if resolved_on is not None and resolved_on < opened:
                resolved_on = opened
            charts, not_in_kairon = parse_charts_cell(row.get("charts_available"))
            category = categories.match(row.get("issue_category"))
            values = {
                "user_id": user.id, "practice_id": practice.id, "issue_category_id": category.id if category else None,
                "hph_status_note": _text(row.get("hph_status")), "opened_on": opened,
                "status": "resolved" if resolved else "in_progress",
                "resolved_on": (resolved_on or today) if resolved else None,
                "charts_available": charts, "not_in_kairon": not_in_kairon, "follow_up_issue": _text(row.get("follow_up_issue")),
            }
            ticket = existing.get(item["jiraKey"])
            if ticket is None:
                ticket = ItTicket(jira_key=item["jiraKey"], source="import", created_by_id=actor.id, **values)
                db.session.add(ticket)
                existing[item["jiraKey"]] = ticket
                result["created"] += 1
            else:
                for field, value in values.items():
                    setattr(ticket, field, value)
                result["updated"] += 1
            tickets.append((item, ticket, resolved))

        practice_ids = {item["_practice"].id for item, _, _ in tickets}
        assigned = {(a.practice_id, a.user_id) for a in PracticeAssignment.query.filter(
            PracticeAssignment.practice_id.in_(practice_ids)).all()} if practice_ids else set()
        for item, _, resolved in tickets:
            pair = (item["_practice"].id, item["_user"].id)
            if pair in assigned:
                continue
            assigned.add(pair)
            db.session.add(PracticeAssignment(practice_id=pair[0], user_id=pair[1],
                                              access_status=None if resolved else "IT Ticket Received",
                                              access_status_since=None if resolved else today, assigned_by_id=actor.id))
    db.session.flush()
    db.session.add_all([ItTicketEvent(ticket_id=ticket.id, jira_key=ticket.jira_key, action="imported", actor_id=actor.id,
                                      changes={"row": item["rowNumber"], "coder": name_key(item["coderName"])})
                        for item, ticket, _ in tickets])
    if new_practices:
        link_all_kairon_records()
    db.session.commit()
    return result
