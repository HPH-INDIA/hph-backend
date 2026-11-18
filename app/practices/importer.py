"""Practices workbook import (Book6.xlsx layout): preview, then commit.

Rows match an existing practice on TIN, else on the normalised name.
Practice status is never changed by an import; new practices start Active.
Assignment access statuses are copied exactly as given.
"""
import re

from app.cohorts.progression import business_today
from app.extensions import db
from app.it_tickets.names import NameResolver, split_names
from app.practices.models import ACCESS_STATUSES, Practice, PracticeAssignment, practice_name_key
from app.practices.services import (
    build_practice, clean_tin, created_status_change, default_project_id, link_all_kairon_records, scoped_practices,
)

_ACCESS_BY_KEY = {status.lower(): status for status in ACCESS_STATUSES}


def _text(value, max_len=None):
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    if text in ("", "0", "None"):
        return None
    return text[:max_len] if max_len else text


def _int(value):
    try:
        return int(float(str(value).replace(",", ""))) if value not in (None, "") else None
    except ValueError:
        return None


def _access(value):
    return _ACCESS_BY_KEY.get(" ".join(str(value or "").split()).lower())


def parse_access_cell(cell, names):
    """'Access Live' applies to everyone in the column;
    'Name A: Access Live | Name B: Not Found' is per person. Returns {display name: status|None}."""
    text = str(cell or "").strip()
    if not text:
        return {name: None for name in names}
    if ":" not in text:
        status = _access(text)
        return {name: status for name in names}
    result = {name: None for name in names}
    for part in re.split(r"\s*\|\s*", text):
        if ":" in part:
            who, status = part.split(":", 1)
            result[who.strip()] = _access(status)
    return result


def _assignments_for(row):
    people = []
    for names_field, access_field, role in (("coder_1lr", "coder_1lr_access", "coder"), ("qa_2lr", "qa_2lr_access", "qa")):
        names = split_names(row.get(names_field))
        for name, status in parse_access_cell(row.get(access_field), names).items():
            people.append({"rawName": name, "role": role, "accessStatus": status})
    return people


def _analyse(actor, rows, mappings):
    resolver = NameResolver(actor, mappings)
    by_tin, by_key = {}, {}
    for practice in scoped_practices(actor).order_by(Practice.id.asc()).all():
        if practice.tin:
            by_tin[practice.tin] = practice
        by_key.setdefault(practice.name_key, practice)
    seen_tin, seen_key = {}, {}  # earlier rows of this same file
    analysed, unmatched = [], []
    for index, row in enumerate(rows, start=1):
        name = _text(row.get("practice"), 255)
        tin = clean_tin(row.get("tin"))
        key = practice_name_key(name) if name else None
        errors = [] if name else ["Practice name is missing."]
        match = by_tin.get(tin) if tin else None
        if match is None and key:
            match = by_key.get(key)
        duplicate_of = (seen_tin.get(tin) if tin else None) or (seen_key.get(key) if key else None)
        if not errors:
            if tin:
                seen_tin.setdefault(tin, index)
            seen_key.setdefault(key, index)
        assignments = []
        for person in _assignments_for(row):
            user = resolver.resolve(person["rawName"])
            if user is None and person["rawName"] not in unmatched:
                unmatched.append(person["rawName"])
            assignments.append({**person, "userId": user.id if user else None})
        action = "error" if errors else ("update" if match or duplicate_of else "create")
        analysed.append({"rowNumber": index, "action": action, "name": name, "tin": tin, "errors": errors,
                         "assignments": assignments, "duplicateOfRow": duplicate_of,
                         "existingPracticeId": match.id if match else None, "existingPracticeName": match.name if match else None,
                         "_match": match, "_row": row})
    return analysed, unmatched


def preview_practice_import(actor, rows, mappings):
    analysed, unmatched = _analyse(actor, rows, mappings)
    summary = {"create": 0, "update": 0, "error": 0}
    for item in analysed:
        summary[item["action"]] += 1
    return {"rows": [{k: v for k, v in item.items() if not k.startswith("_")} for item in analysed],
            "unmatchedNames": unmatched, "summary": summary}


def _fields(row):
    return {
        "name": _text(row.get("practice"), 255), "tin": row.get("tin"), "worktype": _text(row.get("worktype"), 64),
        "priority_rank": _int(row.get("priority_rank")), "cms_count": _int(row.get("cms")),
        "emr": _text(row.get("emr"), 255), "affiliation": _text(row.get("affiliation"), 255),
        "pipeline_2027": _text(row.get("pipeline_2027"), 128), "filter_72": _text(row.get("filter_72"), 16),
        "onshore_coders": _text(row.get("onshore_coders"), 500), "notes": _text(row.get("notes")),
    }


def _update_practice(actor, practice, values, tin_owners):
    """tin_owners: TIN -> the practice holding it, across every project, kept current as the import runs."""
    for field, value in values.items():
        if field == "tin":
            tin = clean_tin(value)
            if tin and tin != practice.tin and tin not in tin_owners:
                tin_owners.pop(practice.tin, None)
                tin_owners[tin] = practice
                practice.tin = tin
        elif value is not None:
            setattr(practice, field, value)
    practice.name_key = practice_name_key(practice.name)
    practice.updated_by_id = actor.id


def _tin_owners(analysed):
    """One query for every TIN the file mentions, including practices outside the importer's scope."""
    tins = {clean_tin(item["_row"].get("tin")) for item in analysed if item["action"] != "error"} - {None}
    if not tins:
        return {}
    return {practice.tin: practice for practice in Practice.query.filter(Practice.tin.in_(tins)).all()}


def commit_practice_import(actor, rows, mappings):
    """One transaction for the whole file. Rows naming the same practice
    (same TIN or name) are merged into one, and a person listed twice for a
    practice (for example in both the 1LR and QA columns, or under two
    spellings mapped to the same user) gets a single assignment."""
    analysed, _ = _analyse(actor, rows, mappings)
    result = {"created": 0, "updated": 0, "assignmentsCreated": 0, "assignmentsUpdated": 0, "skipped": 0}
    today = business_today()
    # A fixed number of queries however long the file is: practices are built
    # in memory (no autoflush per row) and inserted together in one flush.
    with db.session.no_autoflush:
        tin_owners = _tin_owners(analysed)
        project_id = default_project_id(actor)
        by_row, created = {}, []
        for item in analysed:
            if item["action"] == "error":
                result["skipped"] += 1
                continue
            values = _fields(item["_row"])
            practice = item["_match"] or by_row.get(item["duplicateOfRow"])
            if practice is None:
                tin = clean_tin(values["tin"])
                if tin and tin in tin_owners:
                    values["tin"] = None  # TIN belongs to a practice outside this user's scope
                practice = build_practice(actor, values, project_id=project_id)
                if practice.tin:
                    tin_owners[practice.tin] = practice
                db.session.add(practice)
                created.append(practice)
                result["created"] += 1
            else:
                _update_practice(actor, practice, values, tin_owners)
                if item["_match"] is not None and item["duplicateOfRow"] is None:
                    result["updated"] += 1
            by_row[item["rowNumber"]] = practice
            item["_practice"] = practice
    db.session.flush()
    db.session.add_all([created_status_change(actor, practice) for practice in created])

    practice_ids = {item["_practice"].id for item in analysed if item.get("_practice") is not None}
    existing = {(a.practice_id, a.user_id): a for a in
                PracticeAssignment.query.filter(PracticeAssignment.practice_id.in_(practice_ids)).all()} if practice_ids else {}
    for item in analysed:
        practice = item.get("_practice")
        if practice is None:
            continue
        for person in item["assignments"]:
            if person["userId"] is None:
                continue
            key = (practice.id, person["userId"])
            assignment = existing.get(key)
            if assignment is None:
                assignment = PracticeAssignment(
                    practice_id=practice.id, user_id=person["userId"], access_status=person["accessStatus"],
                    access_status_since=today if person["accessStatus"] else None, assigned_by_id=actor.id)
                db.session.add(assignment)
                existing[key] = assignment
                result["assignmentsCreated"] += 1
            elif person["accessStatus"] and assignment.access_status != person["accessStatus"]:
                assignment.access_status = person["accessStatus"]
                assignment.access_status_since = today
                if assignment.id is not None:
                    result["assignmentsUpdated"] += 1
    db.session.flush()
    link_all_kairon_records()
    db.session.commit()
    return result
