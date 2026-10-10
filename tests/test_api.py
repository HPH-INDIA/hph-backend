import datetime as dt

from app.cohorts.models import Coder, ManualProductionFact, StageTargetRule, UserStagePeriod
from app.encryption.passwords import hash_password
from app.extensions import db
from app.manual_daily_records.services import upsert_own_record
from app.roles.models import Role, RoleType
from app.users.models import Project, User


def _login_superadmin(api_client):
    status, body = api_client.login("superadmin", "superadmin")
    assert status == 200, body
    return body


def test_write_endpoint_rejects_non_superadmin(api_client, employee_user):
    status, body = api_client.login("test-employee@example.com", "test-password")
    assert status == 200, body

    status, body = api_client.post("/api/cohorts", {"window_start": "2026-06-01"})
    assert status == 403, body


def test_superadmin_can_open_cohort_and_list_it(api_client):
    _login_superadmin(api_client)

    status, body = api_client.post("/api/cohorts", {"window_start": "2026-06-01", "label": "API Test Cohort"})
    assert status == 201, body
    cohort_id = body["data"]["id"]
    assert body["data"]["sequence_no"] >= 1

    status, body = api_client.get("/api/cohorts")
    assert status == 200, body
    assert any(c["id"] == cohort_id for c in body["data"])


def test_stage_target_rule_overlap_rejected(api_client):
    _login_superadmin(api_client)

    status, body = api_client.post(
        "/api/stage-target-rules",
        {"stage_code": "M1", "effective_from": "2026-01-01", "daily_target": 9},
    )
    assert status == 409, body
    assert "overlap" in body["message"].lower()


def test_stage_exception_via_api_shifts_only_that_coder(api_client):
    _login_superadmin(api_client)

    coder_a = Coder(full_name="API Test Coder Exception", join_date=dt.date(2026, 1, 1))
    coder_b = Coder(full_name="API Test Coder Peer", join_date=dt.date(2026, 1, 1))
    db.session.add_all([coder_a, coder_b])
    db.session.flush()
    for coder in (coder_a, coder_b):
        db.session.add(
            ManualProductionFact(coder_id=coder.id, activity_date=dt.date(2026, 1, 15), production_count=2)
        )
    db.session.commit()

    # both coders are freshly created outside compute_stage_periods() until
    # something triggers it - drive that the same way the app would: an
    # exception submission recomputes the affected coder only.
    status, body = api_client.post(
        f"/api/coders/{coder_a.id}/stage-exceptions",
        {"stage_code": "Training", "extra_days": 14, "reason": "needs two more weeks"},
    )
    assert status == 201, body

    status, body = api_client.get(f"/api/coders/{coder_a.id}/stage-periods")
    assert status == 200, body
    periods_a = {p["stage_code"]: p for p in body["data"]}
    assert periods_a["Training"]["end_date"] == "2026-01-28"  # 14 (to first activity) + 14 (exception)
    assert periods_a["M1"]["shifted_by_exception_days"] == 14

    status, body = api_client.get(f"/api/coders/{coder_b.id}/stage-periods")
    assert status == 200, body
    assert body["data"] == []  # never recomputed for coder_b - untouched, as expected


def test_ambiguous_join_creates_review_via_api(api_client):
    _login_superadmin(api_client)

    status, body = api_client.post("/api/cohorts", {"window_start": "2026-05-25", "label": "API Prior Cohort"})
    assert status == 201, body
    prior_cohort_id = body["data"]["id"]

    status, body = api_client.post("/api/cohorts", {"window_start": "2026-06-05", "label": "API Current Cohort"})
    assert status == 201, body

    status, body = api_client.post(
        "/api/coders", {"full_name": "API Test Coder Ambiguous", "join_date": "2026-05-30"}
    )
    assert status == 201, body
    coder_id = body["data"]["id"]
    assert body["data"]["cohort_id"] is None

    status, body = api_client.get("/api/cohort-join-review?status=pending")
    assert status == 200, body
    review = next(r for r in body["data"] if r["coder_id"] == coder_id)
    assert prior_cohort_id in review["candidate_cohort_ids"]

    status, body = api_client.post(
        f"/api/cohort-join-review/{review['id']}/resolve", {"cohort_id": prior_cohort_id}
    )
    assert status == 200, body
    assert body["data"]["status"] == "resolved"

    coder = db.session.get(Coder, coder_id)
    assert coder.cohort_id == prior_cohort_id


def test_coding_manager_creates_user_cohort_with_lead_member(api_client, manager_user):
    coding = Project.query.filter_by(name="CODING").one()
    lead_role = Role.query.filter_by(role_type_id=RoleType.query.filter_by(code="lead").one().id).first()
    lead = User.query.filter_by(email="cohort-lead@example.com").first()
    if lead is None:
        lead = User(
            email="cohort-lead@example.com",
            first_name="Cohort",
            last_name="Lead",
            emp_id="TEST-COHORT-LEAD",
            role_id=lead_role.id,
            password_hash=hash_password("test-password"),
            first_login=False,
            is_active=True,
        )
        db.session.add(lead)
    manager_user.project_id = coding.id
    lead.project_id = coding.id
    lead.reports_to_id = manager_user.id
    db.session.commit()

    status, body = api_client.login("test-manager@example.com", "test-password")
    assert status == 200, body
    future_join = dt.date.today() + dt.timedelta(days=10)
    status, body = api_client.post(
        "/api/team/cohorts",
        {"label": "Future Cohort", "windowStart": future_join.isoformat(), "memberIds": [lead.id]},
    )
    assert status == 201, body
    assert body["data"]["memberCount"] == 1
    assert body["data"]["members"][0]["user"]["roleType"] == "lead"
    assert body["data"]["members"][0]["currentStage"] is None
    cohort_id = body["data"]["id"]

    status, overview = api_client.get(f"/api/team/coders?cohortId={cohort_id}&page=1&pageSize=10")
    assert status == 200, overview
    assert overview["data"]["total"] == 1
    row = overview["data"]["items"][0]
    assert row["coder"]["id"] == lead.id
    assert row["cohort"] == {"id": cohort_id, "label": "Future Cohort"}
    assert row["currentStage"] is None
    assert row["dailyTarget"] is None
    assert row["lead"]["id"] == lead.id

    upsert_own_record(
        lead.id,
        {
            "record_date": future_join + dt.timedelta(days=1),
            "production_count": 1,
            "tech_issues_downtime_hours": 0,
            "no_inventory_idle_time_hours": 0,
            "leave_hours": 0,
            "meeting_engagement_hours": 0,
        },
    )
    periods = {period.stage_code: period for period in UserStagePeriod.query.filter_by(user_id=lead.id).all()}
    # Manual production no longer supplies a Kairon completion anchor.
    assert periods["Training"].start_date == future_join
    assert periods["Training"].end_date is None
    assert "M1" not in periods


def test_coding_manager_changes_target_without_rewriting_history(api_client, manager_user):
    coding = Project.query.filter_by(name="CODING").one()
    manager_user.project_id = coding.id
    db.session.commit()
    status, body = api_client.login("test-manager@example.com", "test-password")
    assert status == 200, body

    effective = dt.date.today() + dt.timedelta(days=1)
    status, body = api_client.post(
        "/api/team/stage-targets/change",
        {"stageCode": "M1", "effectiveFrom": effective.isoformat(), "dailyTarget": 10, "reason": "new goal"},
    )
    assert status == 201, body
    assert body["data"]["daily_target"] == 10
    assert body["data"]["effective_from"] == effective.isoformat()

    rules = StageTargetRule.query.filter_by(stage_code="M1").order_by(StageTargetRule.effective_from).all()
    assert [(rule.daily_target, rule.effective_to) for rule in rules] == [(7, effective), (10, None)]
