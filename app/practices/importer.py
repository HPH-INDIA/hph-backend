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
from app.practices.services import clean_tin, create_practice, link_kairon_records, scoped_practices

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
    existing = scoped_practices(actor)
    by_tin = {p.tin: p for p in existing.filter(Practice.tin.isnot(None)).all()}
    by_key = {}
    for practice in existing.all():
        by_key.setdefault(practice.name_key, practice)
    analysed, unmatched = [], []
    for index, row in enumerate(rows, start=1):
        name = _text(row.get("practice"), 255)
        tin = clean_tin(row.get("tin"))
        errors = [] if name else ["Practice name is missing."]
        match = by_tin.get(tin) if tin else None
        match = match or by_key.get(practice_name_key(name)) if name else match
        assignments = []
        for person in _assignments_for(row):
            user = resolver.resolve(person["rawName"])
            if user is None and person["rawName"] not in unmatched:
                unmatched.append(person["rawName"])
            assignments.append({**person, "userId": user.id if user else None})
        analysed.append({"rowNumber": index, "action": "error" if errors else ("update" if match else "create"),
                         "name": name, "tin": tin, "errors": errors, "assignments": assignments,
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


def commit_practice_import(actor, rows, mappings):
    analysed, _ = _analyse(actor, rows, mappings)
    result = {"created": 0, "updated": 0, "assignmentsCreated": 0, "assignmentsUpdated": 0, "skipped": 0}
    today = business_today()
    for item in analysed:
        if item["action"] == "error":
            result["skipped"] += 1
            continue
        values = _fields(item["_row"])
        practice = item["_match"]
        if practice is None:  # an earlier row of this same file may have created it
            tin = clean_tin(values["tin"])
            practice = (scoped_practices(actor).filter(Practice.tin == tin).first() if tin else None) or \
                scoped_practices(actor).filter(Practice.name_key == practice_name_key(values["name"])).first()
            if practice is not None:
                item["_match"] = practice
        if practice is None:
            practice = create_practice(actor, values, commit=False)
            result["created"] += 1
        else:
            for field, value in values.items():
                if field == "tin":
                    tin = clean_tin(value)
                    if tin and tin != practice.tin and not Practice.query.filter(Practice.tin == tin, Practice.id != practice.id).first():
                        practice.tin = tin
                elif value is not None:
                    setattr(practice, field, value)
            practice.name_key = practice_name_key(practice.name)
            practice.updated_by_id = actor.id
            link_kairon_records(practice)
            result["updated"] += 1
        for person in item["assignments"]:
            if person["userId"] is None:
                continue
            assignment = PracticeAssignment.query.filter_by(practice_id=practice.id, user_id=person["userId"]).first()
            if assignment is None:
                db.session.add(PracticeAssignment(
                    practice_id=practice.id, user_id=person["userId"], access_status=person["accessStatus"],
                    access_status_since=today if person["accessStatus"] else None, assigned_by_id=actor.id))
                result["assignmentsCreated"] += 1
            elif person["accessStatus"] and assignment.access_status != person["accessStatus"]:
                assignment.access_status = person["accessStatus"]
                assignment.access_status_since = today
                result["assignmentsUpdated"] += 1
        db.session.flush()
    db.session.commit()
    return result
