"""End-to-end regression coverage for lead-owned manual record review."""

import base64
import datetime as dt
from io import BytesIO
from uuid import uuid4

import pytest
from openpyxl import Workbook

from app.encryption.passwords import hash_password
from app.extensions import db
from app.features.models import Feature
from app.manual_daily_records.models import ManualDailyRecord
from app.manual_daily_records.services import MANUAL_UPLOAD_HEADERS
from app.roles.models import Role, RoleType, role_features
from app.users.models import Project, User


DAY = dt.date(2026, 9, 10)
PASSWORD = "test-password"


@pytest.fixture
def manual_team():
    """Fresh identities prevent other tests' persistent users affecting scope."""
    token = uuid4().hex[:12]
    project = Project.query.filter_by(name="CODING").one()
    password_hash = hash_password(PASSWORD)
    roles = {
        code: Role.query.join(RoleType).filter(RoleType.code == code).first()
        for code in ("manager", "lead", "employee")
    }
    users = {}

    def make(name, role="employee", reports_to=None, **attributes):
        user = User(
            email=f"manual-workflow-{token}-{name}@example.com",
            first_name=f"Workflow {token}",
            last_name=name,
            emp_id=f"MW-{token}-{name}",
            role_id=roles[role].id,
            project_id=project.id,
            reports_to_id=reports_to.id if reports_to else None,
            password_hash=password_hash,
            first_login=False,
            is_active=True,
        )
        for key, value in attributes.items():
            setattr(user, key, value)
        db.session.add(user)
        db.session.flush()
        users[name] = user
        return user

    manager = make("manager", "manager")
    lead = make("lead", "lead", manager)
    sibling_lead = make("sibling-lead", "lead", manager)
    make("coder", reports_to=lead)
    make("missing", reports_to=lead)
    make("sibling-coder", reports_to=sibling_lead)
    outside_manager = make("outside-manager", "manager")
    outside_lead = make("outside-lead", "lead", outside_manager)
    make("outside-coder", reports_to=outside_lead)
    db.session.commit()
    return users, make


def _login(api_client, user):
    status, body = api_client.login(user.email, PASSWORD)
    assert status == 200, body


def _record(user, date=DAY, **overrides):
    values = {
        "user_id": user.id,
        "record_date": date,
        "production_count": 12,
        "pvp_count": 8,
        "foundation_count": 4,
        "tech_issues_downtime_hours": 0,
        "no_inventory_idle_time_hours": 0,
        "leave_hours": 0,
        "meeting_engagement_hours": 1,
        "meeting_type": "Huddle",
        "status": "pending",
    }
    values.update(overrides)
    record = ManualDailyRecord(**values)
    db.session.add(record)
    db.session.commit()
    return record


def _body(**overrides):
    data = {
        "date": DAY.isoformat(),
        "pvpCount": 8,
        "foundationCount": 4,
        "techIssuesDowntimeHours": 0,
        "noInventoryIdleTimeHours": 0,
        "leaveHours": 0,
        "meetingEngagementHours": 1,
    }
    data.update(overrides)
    return data


def _bulk_body(action, records):
    if action == "approve":
        return {"ids": [record.id for record in records]}
    return {"items": [{"id": record.id, "reason": "Check hours"} for record in records]}


def _assert_pending(records):
    for record in records:
        db.session.refresh(record)
        assert record.status == "pending"
        assert record.reviewed_by_id is None
        assert record.reviewed_at is None


def test_employee_submission_and_edit_require_fresh_lead_review(api_client, manual_team):
    users, _ = manual_team
    _login(api_client, users["coder"])
    status, body = api_client.post("/api/manual-daily-records", _body(meetingType="Training"))
    assert status == 200, body
    row = body["data"]
    assert row["status"] == "pending"
    assert row["reviewedById"] is None
    assert row["reviewedAt"] is None

    _login(api_client, users["lead"])
    status, body = api_client.post(f"/api/manual-daily-records/{row['id']}/approve")
    assert status == 200, body

    _login(api_client, users["coder"])
    status, body = api_client.post("/api/manual-daily-records", _body(pvpCount=15))
    assert status == 200, body
    edited = body["data"]
    assert edited["id"] == row["id"]
    assert edited["status"] == "pending"
    assert edited["reviewedById"] is None
    assert edited["reviewedAt"] is None
    assert edited["rejectionReason"] is None
    assert edited["meetingType"] == "Training"


def test_lead_self_submission_and_edit_are_autoapproved_with_audit(api_client, manual_team):
    users, _ = manual_team
    lead = users["lead"]
    _login(api_client, lead)
    status, body = api_client.post("/api/manual-daily-records", _body(meetingType="One-O-One"))
    assert status == 200, body
    saved = body["data"]
    assert saved["status"] == "approved"
    assert saved["reviewedById"] == lead.id
    assert saved["reviewedAt"] is not None
    assert saved["rejectionReason"] is None

    # Editing a historical rejection must also clear stale reviewer metadata.
    record = db.session.get(ManualDailyRecord, saved["id"])
    record.status = "rejected"
    record.reviewed_by_id = users["manager"].id
    record.reviewed_at = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
    record.rejection_reason = "Historical rejection"
    db.session.commit()

    status, body = api_client.post("/api/manual-daily-records", _body(pvpCount=19))
    assert status == 200, body
    edited = body["data"]
    assert edited["id"] == saved["id"]
    assert edited["status"] == "approved"
    assert edited["reviewedById"] == lead.id
    assert edited["reviewedAt"] is not None
    assert edited["reviewedAt"] != "2026-09-01T00:00:00+00:00"
    assert edited["rejectionReason"] is None
    assert edited["meetingType"] == "One-O-One"
    assert edited["productionCount"] == 23


@pytest.mark.parametrize("action,expected", [("approve", "approved"), ("reject", "rejected")])
def test_direct_lead_can_review_employee_once(api_client, manual_team, action, expected):
    users, _ = manual_team
    record = _record(users["coder"])
    _login(api_client, users["lead"])

    path = f"/api/manual-daily-records/{record.id}/{action}"
    status, body = api_client.post(path, {"reason": "Check hours"} if action == "reject" else None)
    assert status == 200, body
    reviewed = body["data"]
    assert reviewed["status"] == expected
    assert reviewed["reviewedById"] == users["lead"].id
    assert reviewed["reviewedAt"] is not None
    assert reviewed["meetingType"] == "Huddle"
    assert reviewed["rejectionReason"] == ("Check hours" if action == "reject" else None)

    status, body = api_client.post(path, {"reason": "Again"} if action == "reject" else None)
    assert status == 409, body


@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize("actor", ["manager", "coder", "sibling-lead", "outside-lead"])
def test_individual_review_rejects_everyone_except_direct_lead(api_client, manual_team, action, actor):
    users, _ = manual_team
    record = _record(users["coder"])
    _login(api_client, users[actor])

    status, body = api_client.post(
        f"/api/manual-daily-records/{record.id}/{action}",
        {"reason": "Check hours"} if action == "reject" else None,
    )
    assert status == (403 if actor in {"manager", "coder"} else 404), body
    _assert_pending([record])


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_lead_cannot_review_own_or_another_leads_historical_pending_row(api_client, manual_team, action):
    users, make = manual_team
    # Even a lead assigned below another lead is not an employee review target.
    nested_lead = make("nested-lead", "lead", users["lead"])
    records = [_record(users["lead"]), _record(users["sibling-lead"]), _record(nested_lead)]
    _login(api_client, users["lead"])

    for record in records:
        status, body = api_client.post(
            f"/api/manual-daily-records/{record.id}/{action}",
            {"reason": "Check hours"} if action == "reject" else None,
        )
        assert status == 404, body
    _assert_pending(records)


@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize("actor", ["manager", "coder"])
def test_bulk_review_rejects_manager_and_employee_roles(api_client, manual_team, action, actor):
    users, _ = manual_team
    record = _record(users["coder"])
    _login(api_client, users[actor])

    status, body = api_client.post(
        f"/api/reports/manual/reviews/bulk-{action}", _bulk_body(action, [record])
    )
    assert status == 403, body
    _assert_pending([record])


@pytest.mark.parametrize("action,result_key", [("approve", "approved"), ("reject", "rejected")])
def test_bulk_review_skips_out_of_scope_and_lead_rows(api_client, manual_team, action, result_key):
    users, make = manual_team
    nested_lead = make("nested-lead", "lead", users["lead"])
    accepted = _record(users["coder"])
    forbidden = [
        _record(users[name])
        for name in ("lead", "sibling-lead", "sibling-coder", "outside-coder")
    ] + [_record(nested_lead)]
    _login(api_client, users["lead"])

    status, body = api_client.post(
        f"/api/reports/manual/reviews/bulk-{action}",
        _bulk_body(action, [accepted, *forbidden]),
    )
    assert status == 200, body
    assert body["data"][result_key] == [accepted.id]
    assert {row["id"] for row in body["data"]["skipped"]} == {row.id for row in forbidden}
    assert all(row["reason"] for row in body["data"]["skipped"])
    _assert_pending(forbidden)
    db.session.refresh(accepted)
    assert accepted.status == result_key
    assert accepted.reviewed_by_id == users["lead"].id
    assert accepted.reviewed_at is not None
    assert accepted.meeting_type == "Huddle"
    assert accepted.rejection_reason == ("Check hours" if action == "reject" else None)


@pytest.mark.parametrize(
    "actor,expected",
    [
        ("coder", {"coder"}),
        ("lead", {"lead", "coder"}),
        ("manager", {"manager", "lead", "coder", "sibling-lead", "sibling-coder"}),
    ],
)
def test_manual_record_listing_is_scoped_before_user_filters(api_client, manual_team, actor, expected):
    users, _ = manual_team
    for name in ("manager", "lead", "coder", "sibling-lead", "sibling-coder", "outside-coder"):
        _record(users[name])
    _login(api_client, users[actor])

    status, body = api_client.get("/api/manual-daily-records?fromDate=2026-09-10&toDate=2026-09-10")
    assert status == 200, body
    assert {row["userId"] for row in body["data"]} == {users[name].id for name in expected}

    for query in (
        f"userId={users['outside-coder'].id}",
        f"userIds={users['outside-coder'].id}",
        f"excludeUserIds={users[actor].id}&userIds={users['outside-coder'].id}",
    ):
        status, body = api_client.get(f"/api/manual-daily-records?{query}")
        assert status == 200, body
        assert body["data"] == []


@pytest.mark.parametrize(
    "actor,expected",
    [("lead", {"coder"}), ("manager", {"lead", "coder", "sibling-lead", "sibling-coder"})],
)
def test_review_listing_has_role_appropriate_scope(api_client, manual_team, actor, expected):
    users, _ = manual_team
    for name in ("manager", "lead", "coder", "sibling-lead", "sibling-coder", "outside-coder"):
        _record(users[name])
    _login(api_client, users[actor])

    status, body = api_client.get("/api/reports/manual/reviews?fromDate=2026-09-10&toDate=2026-09-10")
    assert status == 200, body
    assert {row["userId"] for row in body["data"]["items"]} == {users[name].id for name in expected}

    status, body = api_client.get(f"/api/reports/manual/reviews?userId={users['outside-coder'].id}")
    assert status == 200, body
    assert body["data"]["items"] == []


def test_manager_review_lead_filter_cannot_cross_manager_boundary(api_client, manual_team):
    users, _ = manual_team
    for name in ("lead", "coder", "sibling-lead", "sibling-coder", "outside-coder"):
        _record(users[name])
    _login(api_client, users["manager"])

    status, body = api_client.get(f"/api/reports/manual/reviews?leadId={users['lead'].id}")
    assert status == 200, body
    assert {row["userId"] for row in body["data"]["items"]} == {users["lead"].id, users["coder"].id}
    status, body = api_client.get(f"/api/reports/manual/reviews?leadId={users['outside-lead'].id}")
    assert status == 400, body


@pytest.mark.parametrize("path", ["/api/reports/manual/reviews", "/api/reports/manual/team-day?date=2026-09-10"])
def test_employee_cannot_read_team_reports(api_client, manual_team, path):
    users, _ = manual_team
    _login(api_client, users["coder"])
    status, body = api_client.get(path)
    assert status == 403, body


def test_team_day_keeps_lead_separate_and_missing_coder_visible(api_client, manual_team):
    users, _ = manual_team
    lead_record = _record(users["lead"], status="approved")
    coder_record = _record(users["coder"])
    _record(users["missing"], date=DAY - dt.timedelta(days=1))
    _record(users["outside-coder"])
    _login(api_client, users["lead"])

    status, body = api_client.get("/api/reports/manual/team-day?date=2026-09-10")
    assert status == 200, body
    assert body["data"]["date"] == "2026-09-10"
    assert len(body["data"]["teams"]) == 1
    team = body["data"]["teams"][0]
    assert team["lead"] == {
        "id": users["lead"].id,
        "firstName": users["lead"].first_name,
        "lastName": users["lead"].last_name,
        "empId": users["lead"].emp_id,
    }
    assert team["leadRecord"]["id"] == lead_record.id
    coders = {row["user"]["id"]: row for row in team["coders"]}
    assert set(coders) == {users["coder"].id, users["missing"].id}
    assert coders[users["coder"].id]["record"]["id"] == coder_record.id
    assert coders[users["coder"].id]["record"]["meetingType"] == "Huddle"
    assert coders[users["missing"].id]["record"] is None
    assert coders[users["missing"].id]["user"]["empId"] == users["missing"].emp_id


def test_manager_team_day_groups_owned_leads_with_exact_date_and_isolation(api_client, manual_team):
    users, _ = manual_team
    first_lead_record = _record(users["lead"], status="approved")
    first_coder_record = _record(users["coder"])
    _record(users["sibling-lead"], date=DAY - dt.timedelta(days=1), status="approved")
    second_coder_record = _record(users["sibling-coder"])
    _record(users["outside-lead"], status="approved")
    _record(users["outside-coder"])
    _record(users["manager"])
    _login(api_client, users["manager"])

    status, body = api_client.get("/api/reports/manual/team-day?date=2026-09-10")
    assert status == 200, body
    teams = {team["lead"]["id"]: team for team in body["data"]["teams"]}
    assert set(teams) == {users["lead"].id, users["sibling-lead"].id}
    first = teams[users["lead"].id]
    second = teams[users["sibling-lead"].id]
    assert first["leadRecord"]["id"] == first_lead_record.id
    assert second["leadRecord"] is None
    assert {row["user"]["id"] for row in first["coders"]} == {users["coder"].id, users["missing"].id}
    assert {row["user"]["id"] for row in second["coders"]} == {users["sibling-coder"].id}
    assert [row["record"]["id"] for row in first["coders"] if row["record"]] == [first_coder_record.id]
    assert second["coders"][0]["record"]["id"] == second_coder_record.id
    assert all(
        record["date"] == "2026-09-10"
        for team in teams.values()
        for record in [team["leadRecord"], *[row["record"] for row in team["coders"]]]
        if record is not None
    )


def test_team_day_includes_historical_coders_through_last_working_day(api_client, manual_team):
    users, make = manual_team
    historical = make("historical", reports_to=users["lead"], is_active=False, last_working_day=DAY)
    departed = make("departed", reports_to=users["lead"], is_active=False, last_working_day=DAY - dt.timedelta(days=1))
    undated = make("undated", reports_to=users["lead"], is_active=False)
    no_evidence = make("no-evidence", reports_to=users["lead"], is_active=False)
    _record(departed)  # Even a stray post-departure row cannot extend employment.
    undated_record = _record(undated)
    _record(no_evidence, date=DAY - dt.timedelta(days=1))
    _login(api_client, users["lead"])

    status, body = api_client.get("/api/reports/manual/team-day?date=2026-09-10")
    assert status == 200, body
    coders = {row["user"]["id"]: row for row in body["data"]["teams"][0]["coders"]}
    assert historical.id in coders
    assert coders[historical.id]["record"] is None
    assert coders[undated.id]["record"]["id"] == undated_record.id
    assert departed.id not in coders
    assert no_evidence.id not in coders

    status, body = api_client.get("/api/reports/manual/team-day?date=2026-09-11")
    assert status == 200, body
    next_ids = {row["user"]["id"] for row in body["data"]["teams"][0]["coders"]}
    assert historical.id not in next_ids
    assert undated.id not in next_ids


@pytest.mark.parametrize("action,result_key", [("approve", "approved"), ("reject", "rejected")])
def test_team_day_exposes_every_pending_coder_for_bulk_review(api_client, manual_team, action, result_key):
    users, make = manual_team
    records = [_record(users["coder"])]
    for index in range(12):
        records.append(_record(make(f"extra-{index}", reports_to=users["lead"])))
    _record(users["lead"], status="approved")
    _login(api_client, users["lead"])

    status, body = api_client.get("/api/reports/manual/team-day?date=2026-09-10")
    assert status == 200, body
    coders = body["data"]["teams"][0]["coders"]
    pending_ids = {row["record"]["id"] for row in coders if row["record"] and row["record"]["status"] == "pending"}
    assert pending_ids == {record.id for record in records}
    assert len(pending_ids) == 13

    status, body = api_client.post(
        f"/api/reports/manual/reviews/bulk-{action}", _bulk_body(action, records)
    )
    assert status == 200, body
    assert set(body["data"][result_key]) == pending_ids
    assert body["data"]["skipped"] == []
    for record in records:
        db.session.refresh(record)
        assert record.status == result_key
        assert record.reviewed_by_id == users["lead"].id


@pytest.mark.parametrize("query", ["", "?date=invalid", "?date=2026-02-30"])
def test_team_day_requires_valid_selected_date(api_client, manual_team, query):
    users, _ = manual_team
    _login(api_client, users["lead"])
    status, body = api_client.get(f"/api/reports/manual/team-day{query}")
    assert status == 422, body


@pytest.mark.parametrize("can_read", [True, False])
def test_reports_permissions_gate_reads_and_all_review_writes(api_client, manual_team, can_read):
    users, _ = manual_team
    lead = users["lead"]
    record = _record(users["coder"])
    reports = Feature.query.filter_by(codename="reports").one()
    assignment = db.session.execute(
        db.select(role_features.c.can_read, role_features.c.can_write).where(
            role_features.c.role_id == lead.role_id,
            role_features.c.feature_id == reports.id,
        )
    ).one()
    permission = role_features.update().where(
        role_features.c.role_id == lead.role_id,
        role_features.c.feature_id == reports.id,
    )
    try:
        db.session.execute(permission.values(can_read=can_read, can_write=False))
        db.session.commit()
        _login(api_client, lead)
        for path in (
            "/api/manual-daily-records",
            "/api/reports/manual/reviews",
            "/api/reports/manual/team-day?date=2026-09-10",
        ):
            status, body = api_client.get(path)
            assert status == (200 if can_read else 403), body

        for action in ("approve", "reject"):
            status, body = api_client.post(
                f"/api/manual-daily-records/{record.id}/{action}",
                {"reason": "Check hours"} if action == "reject" else None,
            )
            assert status == 403, body
            status, body = api_client.post(
                f"/api/reports/manual/reviews/bulk-{action}", _bulk_body(action, [record])
            )
            assert status == 403, body
        status, body = api_client.post("/api/manual-daily-records", _body())
        assert status == 403, body
        _assert_pending([record])
        assert ManualDailyRecord.query.filter_by(user_id=lead.id).count() == 0
    finally:
        db.session.execute(
            permission.values(can_read=assignment.can_read, can_write=assignment.can_write)
        )
        db.session.commit()


@pytest.mark.parametrize("import_mode", ["workbook", "chunks"])
def test_manager_import_autoapproves_leads_and_preserves_meeting_type_on_change(api_client, manual_team, import_mode):
    users, _ = manual_team
    lead = users["lead"]
    coder = users["coder"]
    manager = users["manager"]
    _login(api_client, manager)

    def upload(production_count, attempt):
        if import_mode == "workbook":
            workbook = Workbook()
            workbook.active.title = "09102026"
            workbook.active.append(MANUAL_UPLOAD_HEADERS)
            for user in (lead, coder):
                workbook.active.append(
                    [user.email, f"{user.first_name} {user.last_name}", production_count, 0, 0, 0, 1, 0]
                )
            output = BytesIO()
            workbook.save(output)
            status, body = api_client.post(
                "/api/manual-daily-records/bulk-upload",
                {
                    "recordDate": DAY.isoformat(),
                    "sourceFilename": f"lead-team-{attempt}.xlsx",
                    "fileBase64": base64.b64encode(output.getvalue()).decode("ascii"),
                },
            )
            assert status == 201, body
        else:
            status, body = api_client.post(
                "/api/manual-daily-records/imports",
                {"sourceFilename": f"lead-team-{attempt}.xlsx", "fileChecksum": str(attempt) * 64, "totalRows": 2},
            )
            assert status == 201, body
            batch_id = body["data"]["id"]
            rows = [
                {
                    "userId": user.id,
                    "date": DAY.isoformat(),
                    "productionCount": production_count,
                    "techIssuesDowntimeHours": 0,
                    "noInventoryIdleTimeHours": 0,
                    "leaveHours": 0,
                    "meetingEngagementHours": 1,
                }
                for user in (lead, coder)
            ]
            status, body = api_client.post(
                f"/api/manual-daily-records/imports/{batch_id}/chunks/0",
                {"checksum": str(attempt) * 64, "rows": rows},
            )
            assert status == 200, body
            status, body = api_client.post(f"/api/manual-daily-records/imports/{batch_id}/complete")
            assert status == 200, body

    upload(12, 1)
    lead_record = ManualDailyRecord.query.filter_by(user_id=lead.id, record_date=DAY).one()
    coder_record = ManualDailyRecord.query.filter_by(user_id=coder.id, record_date=DAY).one()
    assert lead_record.status == "approved"
    assert lead_record.reviewed_by_id == manager.id
    assert lead_record.reviewed_at is not None
    assert lead_record.rejection_reason is None
    _assert_pending([coder_record])

    lead_record.meeting_type = "PKT"
    coder_record.meeting_type = "Training"
    coder_record.status = "approved"
    coder_record.reviewed_by_id = lead.id
    coder_record.reviewed_at = dt.datetime.now(dt.timezone.utc)
    db.session.commit()
    upload(20, 2)
    db.session.refresh(lead_record)
    db.session.refresh(coder_record)
    assert lead_record.status == "approved"
    assert lead_record.reviewed_by_id == manager.id
    assert lead_record.reviewed_at is not None
    assert lead_record.meeting_type == "PKT"
    assert lead_record.production_count == 20
    assert coder_record.meeting_type == "Training"
    assert coder_record.production_count == 20
    _assert_pending([coder_record])
