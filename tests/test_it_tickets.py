"""IT tickets + practices: rules agreed for the CODING blocker tracker."""
import datetime as dt
from uuid import uuid4

import pytest

from app.cohorts.progression import business_today
from app.encryption.passwords import hash_password
from app.extensions import db
from app.it_tickets.keys import normalize_jira_key, parse_charts_cell, parse_import_date, split_jira_keys
from app.it_tickets.models import IssueCategory, ItTicket, ItTicketEvent, ManualRecordTicket
from app.practices.importer import parse_access_cell
from app.practices.models import Practice, PracticeAssignment
from app.roles.models import Role, RoleType
from app.users.models import Project, User

PASSWORD = "test-password"


# ---- pure helpers ------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("VTEC-14546", "VTEC-14546"), ("VTEC 14444", "VTEC-14444"), ("vtec13133", "VTEC-13133"),
    (" VTEC-0042 ", "VTEC-42"), ("ABC-1", None), ("VTEC-1 & VTEC-2", None), ("", None),
])
def test_normalize_jira_key(raw, expected):
    assert normalize_jira_key(raw) == expected


def test_split_jira_keys_handles_tracker_cells():
    assert split_jira_keys("VTEC-6314 & VTEC 18982") == ["VTEC-6314", "VTEC-18982"]
    assert split_jira_keys("VTEC-12265,VTEC-16606 -09/08/2026") == ["VTEC-12265", "VTEC-16606"]
    assert split_jira_keys("VTEC-15218(08/21/2026)") == ["VTEC-15218"]
    assert split_jira_keys("VTEC10366, vtec10366") == ["VTEC-10366"]


def test_import_cell_parsers():
    assert parse_import_date("2026-08-13") == dt.date(2026, 8, 13)
    assert parse_import_date("08/13/2026") == dt.date(2026, 8, 13)
    assert parse_import_date(46247) == dt.date(2026, 8, 13)
    assert parse_import_date("2026-08-13T00:00:00") == dt.date(2026, 8, 13)
    assert parse_charts_cell("1,154") == (1154, False)
    assert parse_charts_cell("Not available in kairon") == (None, True)
    assert parse_charts_cell(None) == (None, False)


def test_parse_access_cell():
    assert parse_access_cell("Access Live", ["A", "B"]) == {"A": "Access Live", "B": "Access Live"}
    assert parse_access_cell("A: IT Ticket Received | B: access live", ["A", "B"]) == {
        "A": "IT Ticket Received", "B": "Access Live"}
    assert parse_access_cell(None, ["A"]) == {"A": None}


# ---- fixtures ----------------------------------------------------------------

@pytest.fixture
def world(app):
    token = uuid4().hex[:8]
    coding = Project.query.filter_by(name="CODING").one()
    rcm = Project.query.filter_by(name="RCM").one()
    roles = {code: Role.query.join(RoleType).filter(RoleType.code == code).first()
             for code in ("employee", "lead", "manager", "admin")}
    password_hash = hash_password(PASSWORD)
    users = {}

    def make(name, role, project):
        user = User(email=f"itt-{token}-{name}@example.com", first_name="Itt", last_name=f"{name}{token}",
                    emp_id=f"IT-{token}-{name}", role_id=roles[role].id, project_id=project.id,
                    password_hash=password_hash, first_login=False, is_active=True)
        db.session.add(user)
        db.session.flush()
        users[name] = user
        return user

    make("coder", "employee", coding)
    make("coder2", "employee", coding)
    make("qa", "lead", coding)
    make("manager", "manager", coding)
    make("rcm_manager", "manager", rcm)
    make("rcm_coder", "employee", rcm)
    make("admin", "admin", coding)
    practice = Practice(name="Lakeview Family Medicine", name_key="lakeview family medicine", tin="861216407",
                        project_id=coding.id, status="Active")
    other = Practice(name="Riverbend Internal Medicine", name_key="riverbend internal medicine",
                     project_id=coding.id, status="Active")
    rcm_practice = Practice(name="Rcm Practice", name_key="rcm practice", project_id=rcm.id, status="Active")
    db.session.add_all([practice, other, rcm_practice])
    db.session.flush()
    for user, target in ((users["coder"], practice), (users["coder"], other), (users["coder2"], practice),
                         (users["qa"], practice), (users["rcm_coder"], rcm_practice)):
        db.session.add(PracticeAssignment(practice_id=target.id, user_id=user.id, access_status="Request to Practice Sent"))
    db.session.commit()
    category = IssueCategory.query.filter_by(name="Invalid credentials").one()
    return {"users": users, "practice": practice, "other": other, "rcm_practice": rcm_practice, "category": category}


def login(app, user):
    from tests.conftest import ApiClient
    client = ApiClient(app.test_client())
    status, _ = client.login(user.email, PASSWORD)
    assert status == 200
    return client


def log_ticket(client, world, key="VTEC 21595", practice=None, **extra):
    body = {"practiceId": (practice or world["practice"]).id, "jiraKey": key,
            "issueCategoryId": world["category"].id, "openedOn": (business_today() - dt.timedelta(days=10)).isoformat()}
    body.update(extra)
    return client.post("/api/it-tickets", body)


# ---- tickets -----------------------------------------------------------------

def test_coder_logs_ticket_key_normalised_and_access_status_set(app, world):
    coder = login(app, world["users"]["coder"])
    status, body = log_ticket(coder, world, chartsAvailable=1154)
    assert status == 201, body
    data = body["data"]
    assert data["jiraKey"] == "VTEC-21595" and data["role"] == "coder" and data["agingDays"] == 10
    assert data["canEdit"] and data["canChangeStatus"] and not data["canDelete"]
    assignment = PracticeAssignment.query.filter_by(practice_id=world["practice"].id,
                                                    user_id=world["users"]["coder"].id).one()
    assert assignment.access_status == "IT Ticket Received"
    assert ItTicketEvent.query.filter_by(ticket_id=data["id"], action="created").count() == 1

    status, body = log_ticket(coder, world, key="vtec-21595")
    assert status == 409


def test_ticket_requires_assigned_active_practice(app, world):
    coder = login(app, world["users"]["coder"])
    status, _ = log_ticket(coder, world, practice=world["rcm_practice"])
    assert status == 422
    status, _ = log_ticket(coder, world, key="ABC-12")
    assert status == 422


def test_visibility_by_role(app, world):
    users = world["users"]
    log_ticket(login(app, users["coder"]), world, key="VTEC-1")
    log_ticket(login(app, users["qa"]), world, key="VTEC-2")
    log_ticket(login(app, users["rcm_coder"]), world, key="VTEC-3", practice=world["rcm_practice"])

    def keys(user, query=""):
        status, body = login(app, user).get(f"/api/it-tickets?pageSize=100{query}")
        assert status == 200, body
        return sorted(t["jiraKey"] for t in body["data"]["items"])

    assert keys(users["coder"]) == ["VTEC-1"]
    assert keys(users["coder"], "&scope=all") == ["VTEC-1"]
    assert keys(users["qa"]) == ["VTEC-2"]
    assert keys(users["manager"]) == ["VTEC-1", "VTEC-2"]
    assert keys(users["rcm_manager"]) == ["VTEC-3"]
    assert keys(users["admin"]) == ["VTEC-1", "VTEC-2", "VTEC-3"]
    assert keys(users["manager"], "&role=qa") == ["VTEC-2"]
    assert keys(users["manager"], f"&userId={users['coder'].id}") == ["VTEC-1"]

    status, _ = login(app, users["coder"]).get("/api/it-tickets/summary")
    assert status == 403
    status, body = login(app, users["manager"]).get("/api/it-tickets/summary")
    assert status == 200 and body["data"]["total"] == 2 and body["data"]["byRole"]["qa"]["total"] == 1


def test_manager_changes_only_status_and_deletes_with_reason(app, world):
    users = world["users"]
    _, created = log_ticket(login(app, users["coder"]), world)
    ticket_id = created["data"]["id"]
    manager = login(app, users["manager"])

    status, _ = manager.patch(f"/api/it-tickets/{ticket_id}", {"hphStatusNote": "edited"})
    assert status == 403
    status, body = manager.patch(f"/api/it-tickets/{ticket_id}", {"status": "resolved"})
    assert status == 200 and body["data"]["status"] == "resolved" and body["data"]["resolvedOn"]

    status, _ = login(app, users["coder"]).delete(f"/api/it-tickets/{ticket_id}", {"reason": "Logged by mistake"})
    assert status == 403
    status, _ = manager.delete(f"/api/it-tickets/{ticket_id}", {"reason": "Because"})
    assert status == 422
    status, _ = manager.delete(f"/api/it-tickets/{ticket_id}", {"reason": "Duplicate of another ticket", "note": "dup"})
    assert status == 200
    assert db.session.get(ItTicket, ticket_id) is None
    deleted = ItTicketEvent.query.filter_by(ticket_id=ticket_id, action="deleted").one()
    assert deleted.reason == "Duplicate of another ticket: dup"

    status, _ = login(app, users["rcm_manager"]).get(f"/api/it-tickets/{ticket_id}")
    assert status == 404


def test_terminating_practice_resolves_open_tickets_and_hides_it(app, world):
    users = world["users"]
    coder = login(app, users["coder"])
    log_ticket(coder, world, key="VTEC-10")
    log_ticket(login(app, users["qa"]), world, key="VTEC-11")
    manager = login(app, users["manager"])

    status, body = manager.post(f"/api/practices/{world['practice'].id}/status",
                                {"status": "Inactive", "reason": "paused"})
    assert status == 200 and body["data"]["autoResolvedCount"] == 0
    status, mine = coder.get("/api/practices/mine")
    assert world["practice"].id in [p["id"] for p in mine["data"]]

    status, body = manager.post(f"/api/practices/{world['practice'].id}/status",
                                {"status": "Terminated", "reason": "closed"})
    assert status == 200 and body["data"]["autoResolvedCount"] == 2
    tickets = ItTicket.query.filter_by(practice_id=world["practice"].id).all()
    assert {t.status for t in tickets} == {"resolved"}
    assert {t.resolution_note for t in tickets} == {"Practice terminated"}
    _, mine = coder.get("/api/practices/mine")
    assert world["practice"].id not in [p["id"] for p in mine["data"]]
    status, _ = log_ticket(coder, world, key="VTEC-12")
    assert status == 422
    status, history = manager.get(f"/api/practices/{world['practice'].id}/history")
    assert history["data"][0]["toStatus"] == "Terminated" and history["data"][0]["autoResolvedCount"] == 2


def test_practice_permissions_and_delete_rules(app, world):
    users = world["users"]
    status, _ = login(app, users["coder"]).get("/api/practices")
    assert status == 403
    manager = login(app, users["manager"])
    status, body = manager.post("/api/practices", {"name": "  New   Clinic ", "tin": "12-3456789"})
    assert status == 201 and body["data"]["tin"] == "123456789"
    new_id = body["data"]["id"]
    status, _ = manager.post("/api/practices", {"name": "Dup", "tin": "123456789"})
    assert status == 409
    status, listing = login(app, users["rcm_manager"]).get("/api/practices?pageSize=100")
    assert new_id not in [p["id"] for p in listing["data"]["items"]]

    log_ticket(login(app, users["coder"]), world, key="VTEC-50")
    status, _ = manager.delete(f"/api/practices/{world['practice'].id}")
    assert status == 409
    status, _ = manager.delete(f"/api/practices/{new_id}")
    assert status == 200

    status, body = manager.post(f"/api/practices/{world['other'].id}/assignments",
                                {"userId": users["qa"].id, "accessStatus": "Access Live"})
    assert status == 201 and body["data"]["role"] == "qa"
    status, _ = manager.post(f"/api/practices/{world['other'].id}/assignments", {"userId": users["qa"].id})
    assert status == 409
    status, _ = manager.post(f"/api/practices/{world['other'].id}/assignments", {"userId": users["rcm_coder"].id})
    assert status == 422


def test_practice_request_flow(app, world):
    users = world["users"]
    status, body = login(app, users["coder2"]).post("/api/practice-requests",
                                                    {"name": "Riverbend internal medicine", "note": "in Kairon"})
    assert status == 201
    request_id = body["data"]["id"]
    manager = login(app, users["manager"])
    _, pending = manager.get("/api/practice-requests")
    item = next(r for r in pending["data"] if r["id"] == request_id)
    assert item["suggestion"]["practiceId"] == world["other"].id
    status, body = manager.post(f"/api/practice-requests/{request_id}/resolve",
                                {"action": "assign", "practiceId": world["other"].id})
    assert status == 200 and body["data"]["status"] == "assigned"
    _, mine = login(app, users["coder2"]).get("/api/practices/mine")
    assert world["other"].id in [p["id"] for p in mine["data"]]


# ---- manual records ----------------------------------------------------------

def _manual(client, day, tickets=None, idle="6", downtime="2", leave="0"):
    body = {"date": day.isoformat(), "pvpCount": 0, "foundationCount": 0, "techIssuesDowntimeHours": downtime,
            "noInventoryIdleTimeHours": idle, "leaveHours": leave, "meetingEngagementHours": "0"}
    if tickets is not None:
        body["tickets"] = tickets
    return client.post("/api/manual-daily-records", body)


def test_full_idle_day_requires_linked_tickets(app, world):
    coder = login(app, world["users"]["coder"])
    _, t1 = log_ticket(coder, world, key="VTEC-70")
    _, t2 = log_ticket(coder, world, key="VTEC-71", practice=world["other"])
    day = business_today() - dt.timedelta(days=2)

    status, body = _manual(coder, day)
    assert status == 422 and "link at least one" in body["message"]
    status, body = _manual(coder, day, [{"ticketId": t1["data"]["id"], "hours": "5"}])
    assert status == 422 and "add up" in body["message"]
    status, body = _manual(coder, day, [{"ticketId": t1["data"]["id"], "hours": "5"},
                                        {"ticketId": t2["data"]["id"], "hours": "3"}])
    assert status == 200, body
    assert sorted(t["jiraKey"] for t in body["data"]["tickets"]) == ["VTEC-70", "VTEC-71"]
    assert ManualRecordTicket.query.count() == 2

    # Half-day leave: 4 h available, 4 h idle -> still a full idle day.
    status, body = _manual(coder, day - dt.timedelta(days=1), idle="4", downtime="0", leave="4")
    assert status == 422
    # Partial idle: optional, but cannot exceed the idle hours.
    status, _ = _manual(coder, day - dt.timedelta(days=2), [{"ticketId": t1["data"]["id"], "hours": "3"}], idle="2", downtime="0")
    assert status == 422
    status, _ = _manual(coder, day - dt.timedelta(days=2), idle="2", downtime="0")
    assert status == 200

    other = login(app, world["users"]["coder2"])
    status, body = _manual(other, day, [{"ticketId": t1["data"]["id"], "hours": "8"}])
    assert status == 422 and "own" in body["message"]


# ---- imports -----------------------------------------------------------------

def test_ticket_import_splits_keys_copies_status_and_updates_on_reimport(app, world):
    coder = world["users"]["coder"]
    manager = login(app, world["users"]["manager"])
    rows = [
        {"coderName": f" {coder.first_name} {coder.last_name}\n", "practiceName": "LAKEVIEW family medicine",
         "issueCategory": "Did not receive login credetials", "hphStatus": "waiting", "ticketId": "VTEC-6314 & VTEC 18982",
         "ticketOpenDate": "2026-04-23", "ticketStatus": "In Progress", "chartsAvailable": "1154"},
        {"coderName": "Unknown Person", "practiceName": "Brand New Practice", "issueCategory": "Time Zone",
         "ticketId": "VTEC13133", "ticketOpenDate": "06/15/2026", "ticketStatus": "Resolved",
         "ticketResolvedDate": "2026-09-18", "chartsAvailable": "Not available in kairon"},
        {"coderName": "X", "practiceName": "Y", "ticketId": "none"},
    ]
    status, preview = manager.post("/api/it-tickets/imports/preview", {"rows": rows})
    assert status == 200, preview
    data = preview["data"]
    assert data["summary"] == {"create": 3, "update": 0, "error": 1}
    assert "Unknown Person" in data["unmatchedNames"] and "Brand New Practice" in data["unmatchedPractices"]

    status, result = manager.post("/api/it-tickets/imports/commit",
                                  {"rows": rows, "userMappings": {"Unknown Person": world["users"]["coder2"].id}})
    assert status == 200, result
    assert result["data"] == {"created": 3, "updated": 0, "practicesCreated": 1, "skipped": 1}
    first, second = ItTicket.query.filter(ItTicket.jira_key.in_(["VTEC-6314", "VTEC-18982"])).all()
    assert {first.status, second.status} == {"in_progress"}
    assert first.issue_category.name == "Did not receive login credentials"
    resolved = ItTicket.query.filter_by(jira_key="VTEC-13133").one()
    assert resolved.status == "resolved" and resolved.resolved_on == dt.date(2026, 9, 18) and resolved.not_in_kairon

    rows[0]["ticketStatus"] = "Resolved"
    rows[0]["ticketResolvedDate"] = "2026-10-01"
    status, result = manager.post("/api/it-tickets/imports/commit",
                                  {"rows": rows[:1]})
    assert result["data"]["updated"] == 2 and result["data"]["created"] == 0
    assert ItTicket.query.filter_by(jira_key="VTEC-6314").one().status == "resolved"

    status, _ = login(app, coder).post("/api/it-tickets/imports/preview", {"rows": rows})
    assert status == 403


def test_practice_import_matches_tin_and_parses_assignments(app, world):
    coder, qa = world["users"]["coder"], world["users"]["qa"]
    manager = login(app, world["users"]["manager"])
    rows = [
        {"worktype": "Pre-Visit Planning", "priorityRank": 1, "tin": 861216407, "practice": "Lakeview Family Medicine PLLC",
         "cms": 87, "coder1lr": f"{coder.first_name} {coder.last_name}", "qa2lr": f"{qa.first_name} {qa.last_name}",
         "emr": "athenaClinicals", "coder1lrAccess": "Access Live", "qa2lrAccess": "Credentials Shared with User",
         "filter72": "Hide"},
        {"worktype": "Foundations", "tin": "999999999", "practice": "Imported Clinic",
         "coder1lr": f"{coder.first_name} {coder.last_name} | Ghost Coder",
         "coder1lrAccess": f"{coder.first_name} {coder.last_name}: IT Ticket Received | Ghost Coder: Not Found"},
    ]
    status, preview = manager.post("/api/practices/imports/preview", {"rows": rows})
    assert status == 200
    assert preview["data"]["summary"] == {"create": 1, "update": 1, "error": 0}
    assert preview["data"]["unmatchedNames"] == ["Ghost Coder"]
    status, result = manager.post("/api/practices/imports/commit", {"rows": rows})
    assert status == 200 and result["data"]["created"] == 1 and result["data"]["updated"] == 1
    lakeview = db.session.get(Practice, world["practice"].id)
    assert lakeview.cms_count == 87 and lakeview.emr == "athenaClinicals" and lakeview.status == "Active"
    statuses = {a.user_id: a.access_status for a in lakeview.assignments}
    assert statuses[coder.id] == "Access Live" and statuses[qa.id] == "Credentials Shared with User"
    imported = Practice.query.filter_by(tin="999999999").one()
    assert [(a.user_id, a.access_status) for a in imported.assignments] == [(coder.id, "IT Ticket Received")]


def test_kairon_records_link_to_practices_by_normalised_name(app, world):
    from app.kairon.models import KaironChartRecord, KaironUploadBatch
    from app.practices.services import link_all_kairon_records

    coder = world["users"]["coder"]
    batch = KaironUploadBatch(uploaded_by_id=coder.id, status="completed")
    db.session.add(batch)
    db.session.flush()
    records = [KaironChartRecord(batch_id=batch.id, program="PVP", level="1LR", status="Active", user_id=coder.id,
                                 coding_analyst_raw="x", created_date=business_today(), practice=name)
               for name in ("  LAKEVIEW   family medicine ", "Brand New Clinic")]
    db.session.add_all(records)
    db.session.commit()
    link_all_kairon_records()
    db.session.commit()
    assert records[0].practice_id == world["practice"].id and records[1].practice_id is None

    status, body = login(app, world["users"]["manager"]).post("/api/practices", {"name": "brand new  clinic"})
    assert status == 201
    db.session.refresh(records[1])
    assert records[1].practice_id == body["data"]["id"]


def test_add_practice_accepts_starting_status(app, world):
    manager = login(app, world["users"]["manager"])
    status, body = manager.post("/api/practices", {"name": "Form Clinic", "tin": "fdas", "worktype": "Pre-Visit Planning",
                                                  "priorityRank": 2, "cmsCount": 2, "filter72": "fdasf", "status": "Active"})
    assert status == 201 and body["data"]["status"] == "Active" and body["data"]["tin"] is None
    status, body = manager.post("/api/practices", {"name": "Paused Clinic", "status": "Inactive"})
    assert status == 201 and body["data"]["status"] == "Inactive"
    status, _ = manager.post("/api/practices", {"name": "Bad", "status": "Closed"})
    assert status == 422


def test_practice_import_merges_duplicate_rows_and_people(app, world):
    """Same person in both the 1LR and QA columns, two spellings mapped to one
    user, and the same practice twice in the file must not fail the import."""
    coder = world["users"]["coder"]
    manager = login(app, world["users"]["manager"])
    rows = [
        {"tin": "111111111", "practice": "Paper Clinic", "coder1lr": "Paper", "qa2lr": "Paper",
         "coder1lrAccess": "Access Live", "qa2lrAccess": "Access Live"},
        {"tin": "222222222", "practice": "Temp Clinic", "coder1lr": "Eric Osorio | Eric Osorio temp assignment"},
        {"tin": "111111111", "practice": "PAPER CLINIC ", "emr": "eCW", "coder1lr": "Paper"},
        {"practice": "Temp  clinic", "notes": "same name, no TIN"},
    ]
    mappings = {"Paper": coder.id, "Eric Osorio": coder.id, "Eric Osorio temp assignment": coder.id}
    status, preview = manager.post("/api/practices/imports/preview", {"rows": rows, "userMappings": mappings})
    assert status == 200
    assert preview["data"]["summary"] == {"create": 2, "update": 2, "error": 0}
    assert [r["duplicateOfRow"] for r in preview["data"]["rows"]] == [None, None, 1, 2]
    status, result = manager.post("/api/practices/imports/commit", {"rows": rows, "userMappings": mappings})
    assert status == 200, result
    assert result["data"]["created"] == 2 and result["data"]["assignmentsCreated"] == 2
    paper = Practice.query.filter_by(tin="111111111").one()
    assert paper.emr == "eCW" and [a.user_id for a in paper.assignments] == [coder.id]
    temp = Practice.query.filter_by(tin="222222222").one()
    assert temp.notes == "same name, no TIN" and len(temp.assignments) == 1


def test_practice_import_query_count_does_not_grow_with_rows(app, world):
    """A full workbook (~330 rows) timed out behind the 60 s proxy because
    every row cost several round trips. The commit must use a fixed number
    of statements however many practices it creates."""
    from sqlalchemy import event

    from app.practices.models import PracticeStatusChange

    coder, qa = world["users"]["coder"], world["users"]["qa"]
    manager = login(app, world["users"]["manager"])
    existing_tin = world["practice"].tin
    rows = [{"tin": f"5{index:08d}", "practice": f"Bulk Clinic {index}", "worktype": "Foundations",
             "coder1lr": f"{coder.first_name} {coder.last_name}", "qa2lr": f"{qa.first_name} {qa.last_name}",
             "coder1lrAccess": "Access Live", "qa2lrAccess": "Access Live"} for index in range(300)]
    rows.append({"tin": existing_tin, "practice": "Lakeview renamed in file", "emr": "Epic"})

    statements = []
    engine = db.engine
    listener = lambda *args: statements.append(args[2])  # noqa: E731
    event.listen(engine, "before_cursor_execute", listener)
    try:
        status, result = manager.post("/api/practices/imports/commit", {"rows": rows})
    finally:
        event.remove(engine, "before_cursor_execute", listener)

    assert status == 200, result
    assert result["data"]["created"] == 300 and result["data"]["updated"] == 1
    assert result["data"]["assignmentsCreated"] == 600
    assert len(statements) < 40, f"{len(statements)} statements for 301 rows"
    assert Practice.query.filter(Practice.name.like("Bulk Clinic %")).count() == 300
    assert PracticeStatusChange.query.join(Practice).filter(Practice.name.like("Bulk Clinic %"),
                                                             PracticeStatusChange.reason == "Created").count() == 300
    assert db.session.get(Practice, world["practice"].id).emr == "Epic"


def test_practice_list_query_count_does_not_grow_with_practices(app, world):
    """After the 334-practice import the list (pageSize=5000) timed out:
    assignments, users and roles were lazy-loaded per practice."""
    from sqlalchemy import event

    coder, qa = world["users"]["coder"], world["users"]["qa"]
    manager = login(app, world["users"]["manager"])
    project_id = world["practice"].project_id
    for index in range(120):
        practice = Practice(name=f"List Clinic {index}", name_key=f"list clinic {index}", tin=f"7{index:08d}",
                            project_id=project_id, status="Active")
        db.session.add(practice)
        db.session.flush()
        db.session.add_all([PracticeAssignment(practice_id=practice.id, user_id=coder.id, access_status="Access Live"),
                            PracticeAssignment(practice_id=practice.id, user_id=qa.id, access_status="Access Live")])
    db.session.commit()
    db.session.expunge_all()

    statements = []
    engine = db.engine
    listener = lambda *args: statements.append(args[2])  # noqa: E731
    event.listen(engine, "before_cursor_execute", listener)
    try:
        status, body = manager.get("/api/practices?status=Active&status=Inactive&page=1&pageSize=5000")
    finally:
        event.remove(engine, "before_cursor_execute", listener)

    assert status == 200, body
    listed = [p for p in body["data"]["items"] if p["name"].startswith("List Clinic")]
    assert len(listed) == 120 and all(len(p["assignments"]) == 2 for p in listed)
    assert {a["role"] for p in listed for a in p["assignments"]} == {"coder", "qa"}
    assert len(statements) < 30, f"{len(statements)} statements to list {len(body['data']['items'])} practices"


def test_ticket_import_matches_similar_practice_names(app, world):
    """'LAKEVIEW FAMILY MEDICINE, PLLC.' in the tracker is the master list's
    'Lakeview Family Medicine': no duplicate practice is created."""
    coder = world["users"]["coder"]
    manager = login(app, world["users"]["manager"])
    rows = [{"coderName": f"{coder.first_name} {coder.last_name}", "practiceName": "LAKEVIEW FAMILY MEDICINE, PLLC.",
             "ticketId": "VTEC-31001", "ticketOpenDate": "2026-09-01", "ticketStatus": "In Progress"}]
    status, preview = manager.post("/api/it-tickets/imports/preview", {"rows": rows})
    assert status == 200, preview
    assert preview["data"]["unmatchedPractices"] == []
    assert preview["data"]["similarPractices"] == [{"nameInFile": "LAKEVIEW FAMILY MEDICINE, PLLC.",
                                                    "practiceId": world["practice"].id, "practiceName": "Lakeview Family Medicine"}]
    status, result = manager.post("/api/it-tickets/imports/commit", {"rows": rows})
    assert status == 200 and result["data"]["practicesCreated"] == 0
    assert ItTicket.query.filter_by(jira_key="VTEC-31001").one().practice_id == world["practice"].id


def test_ticket_import_query_count_does_not_grow_with_rows(app, world):
    from sqlalchemy import event

    from app.practices.models import PracticeStatusChange

    coder, qa = world["users"]["coder"], world["users"]["qa"]
    manager = login(app, world["users"]["manager"])
    rows = []
    for index in range(150):
        person = coder if index % 2 else qa
        rows.append({"coderName": f"{person.first_name} {person.last_name}",
                     "practiceName": "Lakeview Family Medicine" if index % 3 else f"Tracker Only Clinic {index % 10}",
                     "issueCategory": "Latency issue", "ticketId": f"VTEC-{40000 + index}", "ticketOpenDate": "2026-08-01",
                     "ticketStatus": "Resolved" if index % 5 == 0 else "In Progress", "ticketResolvedDate": "2026-09-01"})

    statements = []
    engine = db.engine
    listener = lambda *args: statements.append(args[2])  # noqa: E731
    event.listen(engine, "before_cursor_execute", listener)
    try:
        status, result = manager.post("/api/it-tickets/imports/commit", {"rows": rows})
    finally:
        event.remove(engine, "before_cursor_execute", listener)

    assert status == 200, result
    assert result["data"] == {"created": 150, "updated": 0, "practicesCreated": 10, "skipped": 0}
    assert len(statements) < 40, f"{len(statements)} statements for 150 tickets"
    tracker = Practice.query.filter(Practice.name.like("Tracker Only Clinic %")).all()
    assert len(tracker) == 10
    assert PracticeStatusChange.query.filter(PracticeStatusChange.practice_id.in_([p.id for p in tracker])).count() == 10
    assert ItTicketEvent.query.filter(ItTicketEvent.jira_key.like("VTEC-4%"), ItTicketEvent.action == "imported").count() == 150
    pairs = {(a.practice_id, a.user_id) for a in PracticeAssignment.query.filter(
        PracticeAssignment.practice_id.in_([p.id for p in tracker] + [world["practice"].id])).all()}
    assert (world["practice"].id, coder.id) in pairs and (world["practice"].id, qa.id) in pairs
    assert all((p.id, coder.id) in pairs or (p.id, qa.id) in pairs for p in tracker)
    assert ItTicket.query.filter_by(jira_key="VTEC-40000").one().status == "resolved"


def test_ticket_import_suggests_and_applies_practice_mappings(app, world):
    """Unmatched tracker names get a suggestion (EMR notes in brackets and
    typos ignored) but are only mapped when the importer sends a mapping."""
    coder = world["users"]["coder"]
    manager = login(app, world["users"]["manager"])
    name = f"{coder.first_name} {coder.last_name}"
    rows = [
        {"coderName": name, "practiceName": "Riverbend Internal Medicine (eCW)", "ticketId": "VTEC-32001",
         "ticketOpenDate": "2026-09-01", "ticketStatus": "In Progress"},
        {"coderName": name, "practiceName": "Lakeview Famly Medicine Practice", "ticketId": "VTEC-32002",
         "ticketOpenDate": "2026-09-01", "ticketStatus": "In Progress"},
        {"coderName": name, "practiceName": "Totally Different Clinic", "ticketId": "VTEC-32003",
         "ticketOpenDate": "2026-09-01", "ticketStatus": "In Progress"},
    ]
    status, preview = manager.post("/api/it-tickets/imports/preview", {"rows": rows})
    assert status == 200, preview
    data = preview["data"]
    assert data["unmatchedPractices"] == ["Riverbend Internal Medicine (eCW)", "Lakeview Famly Medicine Practice", "Totally Different Clinic"]
    assert data["practiceSuggestions"] == [
        {"nameInFile": "Riverbend Internal Medicine (eCW)", "practiceId": world["other"].id, "practiceName": "Riverbend Internal Medicine"},
        {"nameInFile": "Lakeview Famly Medicine Practice", "practiceId": world["practice"].id, "practiceName": "Lakeview Family Medicine"},
    ]

    # Another project's practice can't be chosen.
    status, body = manager.post("/api/it-tickets/imports/commit",
                                {"rows": rows, "practiceMappings": {"Totally Different Clinic": world["rcm_practice"].id}})
    assert status == 422, body
    assert ItTicket.query.filter(ItTicket.jira_key.in_(["VTEC-32001", "VTEC-32002", "VTEC-32003"])).count() == 0

    mappings = {"Riverbend Internal Medicine (eCW)": world["other"].id, "lakeview famly  medicine practice": world["practice"].id}
    status, result = manager.post("/api/it-tickets/imports/commit", {"rows": rows, "practiceMappings": mappings})
    assert status == 200, result
    assert result["data"]["created"] == 3 and result["data"]["practicesCreated"] == 1
    tickets = {t.jira_key: t.practice_id for t in ItTicket.query.filter(ItTicket.jira_key.like("VTEC-3200%")).all()}
    assert tickets["VTEC-32001"] == world["other"].id and tickets["VTEC-32002"] == world["practice"].id
    assert Practice.query.filter_by(name="Totally Different Clinic").count() == 1
