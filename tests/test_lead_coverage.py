"""Workflow, authorization, time boundaries, and concurrent grant regression tests."""

import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from werkzeug.exceptions import HTTPException

from app.encryption.passwords import hash_password
from app.extensions import db
from app.features.models import Feature
from app.lead_coverage import services
from app.lead_coverage.models import LeadCoverage
from app.roles.models import Role, RoleType, role_features
from app.users.models import Project, User


TODAY = dt.date(2026, 10, 11)
PASSWORD = "test-password"


@pytest.fixture
def coverage_team(monkeypatch):
    monkeypatch.setattr(services, "business_today", lambda: TODAY)
    monkeypatch.setattr("app.lead_coverage.routes.business_today", lambda: TODAY)
    token = uuid4().hex[:10]
    project = Project.query.filter_by(name="CODING").one()
    roles = {code: Role.query.join(RoleType).filter(RoleType.code == code).first()
             for code in ("manager", "lead", "employee")}
    password_hash = hash_password(PASSWORD)
    users = {}

    def make(name, role="lead", manager=None, **values):
        user = User(
            email=f"coverage-{token}-{name}@example.com", first_name="Coverage", last_name=name,
            emp_id=f"CV-{token}-{name}", role_id=roles[role].id, project_id=project.id,
            reports_to_id=manager.id if manager else None, password_hash=password_hash,
            first_login=False, is_active=True,
        )
        for key, value in values.items():
            setattr(user, key, value)
        db.session.add(user)
        db.session.flush()
        users[name] = user
        return user

    manager = make("manager", "manager")
    other_manager = make("other-manager", "manager")
    for name in ("source", "covering", "third"):
        make(name, manager=manager)
    make("outside", manager=other_manager)
    make("employee", "employee", users["source"])
    make("inactive", manager=manager, is_active=False)
    make("departed", manager=manager, last_working_day=TODAY - dt.timedelta(days=1))
    db.session.commit()
    return users, make


def _login(client, user):
    status, body = client.login(user.email, PASSWORD)
    assert status == 200, body


def _payload(users, **changes):
    data = {"sourceLeadId": users["source"].id, "coveringLeadId": users["covering"].id,
            "startsOn": TODAY.isoformat(), "endsOn": (TODAY + dt.timedelta(days=2)).isoformat(),
            "reason": "Annual leave"}
    data.update(changes)
    return data


def _create(client, users, actor="source", **changes):
    _login(client, users[actor])
    status, body = client.post("/api/lead-coverage", _payload(users, **changes))
    assert status == 201, body
    return body["data"]


def test_lead_request_only_activates_after_recipient_accepts(api_client, coverage_team):
    users, _ = coverage_team
    row = _create(api_client, users)
    assert row["status"] == "pending"
    assert not row["isActive"] and not row["canApprove"] and row["canCancel"]
    assert row["sourceLead"]["id"] == users["source"].id
    assert services.active_covered_lead_ids(users["covering"]) == []
    _login(api_client, users["covering"])
    status, body = api_client.get("/api/lead-coverage")
    assert status == 200 and body["data"][0]["canApprove"]
    status, body = api_client.post(f"/api/lead-coverage/{row['id']}/approve")
    assert status == 200, body
    approved = body["data"]
    assert approved["status"] == "approved" and approved["isActive"]
    assert approved["decidedById"] == users["covering"].id and approved["decidedAt"]
    assert services.active_covered_lead_ids(users["covering"]) == [users["source"].id]
    assert services.active_covered_lead_ids(users["source"]) == []
    status, _ = api_client.post(f"/api/lead-coverage/{row['id']}/approve")
    assert status == 409


def test_manager_assignment_is_immediate_and_audited(api_client, coverage_team):
    users, _ = coverage_team
    row = _create(api_client, users, "manager")
    assert row["status"] == "approved" and row["isActive"]
    assert row["createdById"] == row["decidedById"] == users["manager"].id
    assert row["decidedAt"]
    assert services.active_covered_lead_ids(users["covering"]) == [users["source"].id]


@pytest.mark.parametrize("actor,expected", [("source", 403), ("third", 404), ("manager", 403), ("outside", 404), ("employee", 403)])
def test_only_recipient_can_decide(api_client, coverage_team, actor, expected):
    users, _ = coverage_team
    row = _create(api_client, users)
    _login(api_client, users[actor])
    for action in ("approve", "reject"):
        status, body = api_client.post(f"/api/lead-coverage/{row['id']}/{action}")
        assert status == expected, body
    assert db.session.get(LeadCoverage, row["id"]).status == "pending"


def test_rejection_and_cancellation_are_terminal_with_audit(api_client, coverage_team):
    users, _ = coverage_team
    row = _create(api_client, users)
    _login(api_client, users["covering"])
    status, body = api_client.post(f"/api/lead-coverage/{row['id']}/reject")
    assert status == 200 and body["data"]["status"] == "rejected"
    assert body["data"]["decidedById"] == users["covering"].id
    assert services.active_covered_lead_ids(users["covering"]) == []
    status, _ = api_client.post(f"/api/lead-coverage/{row['id']}/cancel")
    assert status == 409


@pytest.mark.parametrize("actor", ["source", "covering", "manager"])
def test_either_lead_or_manager_can_revoke_and_keep_approval_audit(api_client, coverage_team, actor):
    users, _ = coverage_team
    row = _create(api_client, users, "manager")
    _login(api_client, users[actor])
    status, body = api_client.post(f"/api/lead-coverage/{row['id']}/cancel")
    assert status == 200, body
    cancelled = body["data"]
    assert cancelled["status"] == "cancelled" and not cancelled["isActive"]
    assert cancelled["decidedById"] == row["decidedById"]
    assert cancelled["decidedAt"] == row["decidedAt"]
    assert cancelled["cancelledById"] == users[actor].id and cancelled["cancelledAt"]
    assert services.active_covered_lead_ids(users["covering"]) == []
    status, _ = api_client.post(f"/api/lead-coverage/{row['id']}/cancel")
    assert status == 409


@pytest.mark.parametrize("actor", ["third", "outside", "other-manager"])
def test_unrelated_actor_cannot_read_or_cancel(api_client, coverage_team, actor):
    users, _ = coverage_team
    row = _create(api_client, users, "manager")
    _login(api_client, users[actor])
    status, body = api_client.get("/api/lead-coverage")
    assert status == 200 and body["data"] == []
    status, _ = api_client.post(f"/api/lead-coverage/{row['id']}/cancel")
    assert status == 404


@pytest.mark.parametrize("covering", ["source", "outside", "employee", "inactive", "departed"])
def test_invalid_recipient_is_rejected(api_client, coverage_team, covering):
    users, _ = coverage_team
    _login(api_client, users["source"])
    status, body = api_client.post("/api/lead-coverage", _payload(users, coveringLeadId=users[covering].id))
    assert status == 400, body


def test_sources_are_owned_and_manager_cannot_cross_project(api_client, coverage_team):
    users, make = coverage_team
    _login(api_client, users["covering"])
    status, _ = api_client.post("/api/lead-coverage", _payload(users))
    assert status == 403
    _login(api_client, users["other-manager"])
    status, _ = api_client.post("/api/lead-coverage", _payload(users))
    assert status == 403
    project = Project(name=f"COVERAGE-{uuid4().hex[:12]}")
    db.session.add(project)
    db.session.flush()
    other_project = make("other-project", manager=users["manager"], project_id=project.id)
    db.session.commit()
    _login(api_client, users["manager"])
    status, _ = api_client.post("/api/lead-coverage", _payload(users, coveringLeadId=other_project.id))
    assert status == 400


@pytest.mark.parametrize("changes,expected", [
    ({"startsOn": "2026-10-10"}, 400),
    ({"endsOn": "2026-10-10"}, 400),
    ({"startsOn": "2026-02-30"}, 422),
    ({"coveringLeadId": -1}, 422),
])
def test_date_and_input_validation(api_client, coverage_team, changes, expected):
    users, _ = coverage_team
    _login(api_client, users["source"])
    status, body = api_client.post("/api/lead-coverage", _payload(users, **changes))
    assert status == expected, body


def test_active_dates_are_inclusive_and_expired_approval_fails(api_client, coverage_team, monkeypatch):
    users, _ = coverage_team
    row = _create(api_client, users, "manager", startsOn="2026-10-12", endsOn="2026-10-13")
    assert not row["isActive"]
    for date, expected in [(dt.date(2026, 10, 12), True), (dt.date(2026, 10, 13), True), (dt.date(2026, 10, 14), False)]:
        monkeypatch.setattr(services, "business_today", lambda: date)
        assert bool(services.active_covered_lead_ids(users["covering"])) is expected
    monkeypatch.setattr(services, "business_today", lambda: TODAY)
    row = _create(api_client, users, startsOn="2026-10-14", endsOn="2026-10-14")
    monkeypatch.setattr(services, "business_today", lambda: dt.date(2026, 10, 15))
    _login(api_client, users["covering"])
    status, _ = api_client.post(f"/api/lead-coverage/{row['id']}/approve")
    assert status == 409


@pytest.mark.parametrize("changed", ["inactive", "project", "manager", "role", "departed", "manager-inactive"])
def test_current_eligibility_changes_revoke_access(api_client, coverage_team, changed):
    users, _ = coverage_team
    _create(api_client, users, "manager")
    source = users["source"]
    if changed == "inactive":
        source.is_active = False
    elif changed == "project":
        source.project_id = None
    elif changed == "manager":
        source.reports_to_id = users["other-manager"].id
    elif changed == "role":
        source.role_id = users["employee"].role_id
    elif changed == "departed":
        source.last_working_day = TODAY - dt.timedelta(days=1)
    else:
        users["manager"].is_active = False
    db.session.commit()
    assert services.active_covered_lead_ids(users["covering"]) == []


def test_overlap_chain_reverse_and_duplicate_requests_are_rejected(api_client, coverage_team):
    users, _ = coverage_team
    row = _create(api_client, users)
    status, _ = api_client.post("/api/lead-coverage", _payload(users))
    assert status == 409
    _login(api_client, users["covering"])
    status, body = api_client.post(f"/api/lead-coverage/{row['id']}/approve")
    assert status == 200, body
    _login(api_client, users["manager"])
    for source, covering in [("source", "third"), ("covering", "source"), ("covering", "third"), ("third", "source")]:
        status, body = api_client.post("/api/lead-coverage", _payload(users, sourceLeadId=users[source].id, coveringLeadId=users[covering].id))
        assert status == 409, body
    # Adjacent periods and a covering lead receiving multiple teams are valid.
    status, body = api_client.post("/api/lead-coverage", _payload(users, startsOn="2026-10-14", endsOn="2026-10-14"))
    assert status == 201, body
    status, body = api_client.post("/api/lead-coverage", _payload(users, sourceLeadId=users["third"].id))
    assert status == 201, body


def test_second_pending_request_rechecks_conflicts_at_approval(api_client, coverage_team):
    users, _ = coverage_team
    first = _create(api_client, users)
    second = _create(api_client, users, coveringLeadId=users["third"].id)
    _login(api_client, users["covering"])
    assert api_client.post(f"/api/lead-coverage/{first['id']}/approve")[0] == 200
    _login(api_client, users["third"])
    assert api_client.post(f"/api/lead-coverage/{second['id']}/approve")[0] == 409


def test_options_excludes_ineligible_leads(api_client, coverage_team):
    users, _ = coverage_team
    _login(api_client, users["source"])
    status, body = api_client.get("/api/lead-coverage/options")
    assert status == 200, body
    assert {lead["id"] for lead in body["data"]["leads"]} == {users["covering"].id, users["third"].id}
    assert body["data"]["today"] == TODAY.isoformat()
    assert body["data"]["timezone"] == "Asia/Kolkata"


def test_reports_read_only_allows_requests_and_cancel_but_not_approval(api_client, coverage_team):
    users, _ = coverage_team
    feature = Feature.query.filter_by(codename="reports").one()
    read_only_role = Role(role_type_id=users["source"].role.role_type_id,
                          title=f"Coverage read only {uuid4().hex[:10]}")
    db.session.add(read_only_role)
    db.session.flush()
    role_id = read_only_role.id
    db.session.execute(role_features.insert().values(role_id=role_id, feature_id=feature.id,
                                                    can_read=True, can_write=False))
    users["source"].role_id = role_id
    db.session.commit()
    where = (role_features.c.role_id == role_id) & (role_features.c.feature_id == feature.id)
    original = db.session.execute(db.select(role_features.c.can_read, role_features.c.can_write).where(where)).one()
    try:
        db.session.execute(role_features.update().where(where).values(can_read=True, can_write=False))
        db.session.commit()
        row = _create(api_client, users)
        # Permission can also be withdrawn after a request was created.
        users["covering"].role_id = role_id
        db.session.commit()
        _login(api_client, users["covering"])
        assert api_client.post(f"/api/lead-coverage/{row['id']}/approve")[0] == 403
        assert api_client.post(f"/api/lead-coverage/{row['id']}/reject")[0] == 403
        assert api_client.post(f"/api/lead-coverage/{row['id']}/cancel")[0] == 200
        db.session.execute(role_features.update().where(where).values(can_read=False, can_write=False))
        db.session.commit()
        assert api_client.get("/api/lead-coverage")[0] == 403
        assert api_client.post("/api/lead-coverage", _payload(users))[0] == 403
    finally:
        db.session.execute(role_features.update().where(where).values(can_read=original.can_read, can_write=original.can_write))
        db.session.commit()


@pytest.mark.parametrize("actor", ["source", "manager"])
def test_read_only_recipient_cannot_receive_new_requests_or_assignments(api_client, coverage_team, actor):
    users, _ = coverage_team
    reports = Feature.query.filter_by(codename="reports").one()
    role = Role(role_type_id=users["covering"].role.role_type_id,
                title=f"Coverage recipient read only {uuid4().hex[:10]}")
    db.session.add(role)
    db.session.flush()
    db.session.execute(role_features.insert().values(role_id=role.id, feature_id=reports.id,
                                                    can_read=True, can_write=False))
    users["covering"].role_id = role.id
    db.session.commit()
    _login(api_client, users[actor])
    status, body = api_client.post("/api/lead-coverage", _payload(users))
    assert status == 400 and "write access to Reports" in body["message"]
    status, body = api_client.get("/api/lead-coverage/options")
    assert status == 200, body
    option_ids = {lead["id"] for lead in body["data"]["leads"]}
    assert (users["covering"].id in option_ids) is (actor == "manager")
    if actor == "manager":
        assert next(lead for lead in body["data"]["leads"] if lead["id"] == users["covering"].id)["canCover"] is False


@pytest.mark.parametrize("pair_names", [(("source", "covering"), ("source", "third")), (("source", "covering"), ("covering", "third"))])
def test_concurrent_assignments_cannot_double_grant_or_chain(app, coverage_team, pair_names):
    users, _ = coverage_team
    manager_id = users["manager"].id
    pairs = [(users[source].id, users[covering].id) for source, covering in pair_names]
    # Release the fixture's read transaction before independently connected writers.
    db.session.commit()
    barrier = Barrier(2)

    def assign(pair):
        with app.app_context():
            actor = db.session.get(User, manager_id)
            barrier.wait(timeout=10)
            try:
                services.create_coverage(actor, {"source_lead_id": pair[0], "covering_lead_id": pair[1],
                                               "starts_on": TODAY, "ends_on": TODAY})
                return 201
            except HTTPException as error:
                db.session.rollback()
                return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(assign, pairs))
    assert sorted(results) == [201, 409]
    assert LeadCoverage.query.filter_by(status="approved").count() == 1
