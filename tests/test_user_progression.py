from datetime import date, timedelta
from uuid import uuid4

import pytest

from app.cohorts.models import FoundationTargetRule, UserStageEvidence, UserStagePeriod
from app.cohorts.progression import business_today, foundation_progress, main_periods, period_on
from app.cohorts.stage_refresh import refresh_all_user_stages, rebuild_main_periods
from app.extensions import db
from app.kairon.models import KaironUploadBatch, KaironChartRecord
from app.kairon.services import start_cumulative_import, process_import_chunk, complete_cumulative_import, import_batch
from app.manual_daily_records.services import upsert_own_record
from app.roles.models import Role, RoleType
from app.users.models import User


def user_for(manager, role_type="lead"):
    token = uuid4().hex[:12]
    role = Role.query.join(RoleType).filter(RoleType.code == role_type).first()
    user = User(email=f"{token}@example.test", emp_id=token, first_name=token, last_name="Stage",
                role_id=role.id, project_id=manager.project_id, reports_to_id=manager.id,
                password_hash="unused", first_login=False, join_date=date(2026, 4, 6))
    db.session.add(user)
    db.session.flush()
    return user


def chart(user, **overrides):
    row = dict(mbi="TEST-CHART", program="PVP", level="1LR", status="Completed",
               coding_analyst=f"{user.first_name} {user.last_name}", actions=1,
               created_date=date(2026, 4, 18), completed_date=date(2026, 4, 20))
    row.update(overrides)
    return row


def upload(actor, rows, finish=True):
    batch = start_cumulative_import("stage-test.csv", uuid4().hex * 2, len(rows), actor.id)
    process_import_chunk(batch.id, 1, uuid4().hex * 2, rows)
    if finish:
        complete_cumulative_import(batch.id)
    return batch


def periods(user):
    return {p.stage_code: p for p in UserStagePeriod.query.filter_by(user_id=user.id).all()}


def test_main_day_boundaries_and_known_training_start():
    joined, first = date(2026, 4, 6), date(2026, 4, 20)
    path = main_periods(joined, first)
    assert path[0]["start_date"] == joined
    assert path[0]["end_date"] == date(2026, 4, 19)
    for offset, expected in [(0, "M1"), (29, "M1"), (30, "M2"), (59, "M2"), (60, "M3"), (89, "M3"), (90, "M4"), (119, "M4"), (120, "Steady State")]:
        assert period_on(path, first + timedelta(days=offset))["stage_code"] == expected
    assert main_periods(joined, None)[0]["end_date"] is None
    assert [p["stage_code"] for p in main_periods(joined, joined)][0] == "M1"
    assert all(p["end_date"] is None or p["end_date"] >= p["start_date"] for p in path)


@pytest.mark.parametrize("offset,expected", [(0,"W1"),(6,"W1"),(7,"W2"),(13,"W2"),(14,"W3"),(20,"W3"),(21,"W4"),(27,"W4"),(28,"Steady State")])
def test_foundation_calendar_boundaries(offset, expected):
    first = date(2026, 9, 16)
    result = foundation_progress(date(2026, 5, 21), first, first + timedelta(days=offset))
    assert result["current_stage"] == expected
    # The Foundation transition never mutates the independently calculated M stage.
    assert period_on(main_periods(date(2026, 9, 1), date(2026, 9, 10)), first + timedelta(days=offset))["stage_code"] in ("M1", "M2")


@pytest.mark.parametrize("pvp,foundation,expected", [
    (None,None,"awaiting_foundation"), (date(2026,4,20),None,"awaiting_foundation"),
    (None,date(2026,9,16),"foundation_first"), (date(2026,9,17),date(2026,9,16),"foundation_first"),
    (date(2026,9,16),date(2026,9,16),"same_day_unknown"),
])
def test_foundation_eligibility(pvp, foundation, expected):
    result = foundation_progress(pvp, foundation, date(2026,10,8))
    assert result["eligibility"] == expected
    assert result["periods"] == []
    assert result["current_stage"] is None


def test_departure_caps_both_timelines():
    last = date(2026,9,15)
    path = main_periods(date(2026,5,4),date(2026,5,21),last)
    assert path[-1]["stage_code"] == "M4"
    assert path[-1]["end_date"] == last
    assert period_on(path,last + timedelta(days=1)) is None
    foundation = foundation_progress(date(2026,5,21),date(2026,9,10),last,last)
    assert foundation["current_stage"] == "W1"
    assert len(foundation["periods"]) == 1
    assert foundation["periods"][0]["end_date"] == last


def test_refresh_uses_completion_not_created_or_manual_and_waits_for_finalization(manager_user):
    user = user_for(manager_user)
    rebuild_main_periods(user)
    db.session.commit()
    batch = upload(manager_user,[chart(user), chart(user,mbi="FOUNDATION",program="FOUNDATION",created_date=date(2026,4,1),completed_date=date(2026,9,16))],finish=False)
    assert list(periods(user)) == ["Training"]
    assert db.session.get(UserStageEvidence,user.id) is None
    complete_cumulative_import(batch.id)
    assert periods(user)["M1"].start_date == date(2026,4,20)
    ev = db.session.get(UserStageEvidence,user.id)
    assert ev.first_foundation_completed == date(2026,9,16)
    upsert_own_record(user.id,dict(record_date=date(2026,4,7),production_count=5,tech_issues_downtime_hours=0,
                                 no_inventory_idle_time_hours=0,leave_hours=0,meeting_engagement_hours=0))
    assert periods(user)["M1"].start_date == date(2026,4,20)


def test_corrections_reassignment_and_retries_refresh_old_owner(manager_user):
    first, second = user_for(manager_user), user_for(manager_user)
    row = chart(first)
    upload(manager_user,[row])
    upload(manager_user,[dict(row,completed_date=date(2026,4,22))])
    assert periods(first)["M1"].start_date == date(2026,4,22)
    batch = upload(manager_user,[dict(row,coding_analyst=f"{second.first_name} {second.last_name}")])
    assert list(periods(first)) == ["Training"]
    assert db.session.get(UserStageEvidence,first.id).first_completed is None
    assert periods(second)["M1"].start_date == date(2026,4,20)
    complete_cumulative_import(batch.id)
    assert len(periods(second)) == 6
    upload(manager_user,[dict(row,coding_analyst=f"{second.first_name} {second.last_name}", status="Active",completed_date=None)])
    assert list(periods(second)) == ["Training"]


def test_legacy_snapshot_refresh_and_superseded_completions(manager_user):
    user = user_for(manager_user)
    old = import_batch(date(2026,9,1),[chart(user)],manager_user.id)
    assert old.status == "completed"
    assert periods(user)["M1"].start_date == date(2026,4,20)
    import_batch(date(2026,9,1),[chart(user,completed_date=date(2026,4,25))],manager_user.id)
    assert periods(user)["M1"].start_date == date(2026,4,25)


def test_stage_refresh_failure_does_not_publish_completed_status(manager_user, monkeypatch):
    user = user_for(manager_user)
    batch = upload(manager_user,[chart(user)],finish=False)
    def fail():
        raise RuntimeError("refresh failed")
    monkeypatch.setattr("app.cohorts.stage_refresh.refresh_all_user_stages",fail)
    with pytest.raises(RuntimeError):
        complete_cumulative_import(batch.id)
    db.session.rollback()
    assert db.session.get(KaironUploadBatch,batch.id).status == "uploading"


def test_teams_includes_no_cohort_training_and_departed_user(api_client, manager_user):
    training, left = user_for(manager_user), user_for(manager_user)
    training.join_date = date(2026,7,6)
    left.is_active = False
    left.last_working_day = date(2026,9,15)
    upload(manager_user,[chart(left,completed_date=date(2026,5,21))])
    api_client.login(manager_user.email,"test-password")
    status,body = api_client.get("/api/team/coders?pageSize=100")
    assert status == 200, body
    rows = {r["coder"]["id"]:r for r in body["data"]["items"]}
    for page in range(2, body["data"]["totalPages"] + 1):
        status, next_page = api_client.get(f"/api/team/coders?pageSize=100&page={page}")
        assert status == 200, next_page
        rows.update({r["coder"]["id"]:r for r in next_page["data"]["items"]})
    assert rows[training.id]["periods"][0]["startDate"] == "2026-07-06"
    assert rows[training.id]["currentStage"] == "Training"
    assert rows[left.id]["coder"]["lastWorkingDay"] == "2026-09-15"
    assert rows[left.id]["currentStage"] == "M4"
    assert rows[left.id]["periods"][-1]["endDate"] == "2026-09-15"


def test_foundation_target_versions_validation_and_access(api_client, manager_user, employee_user):
    api_client.login(manager_user.email,"test-password")
    tomorrow = business_today() + timedelta(days=1)
    payload = dict(stageCode="W1",effectiveFrom=tomorrow.isoformat(),dailyTarget=9,reason="New Foundation goal")
    status,body = api_client.post("/api/team/foundation-targets/change",payload)
    assert status == 201,body
    old,new = FoundationTargetRule.query.filter_by(stage_code="W1").order_by(FoundationTargetRule.effective_from).all()
    assert old.daily_target == 7 and old.effective_to == tomorrow
    assert new.daily_target == 9 and new.created_by_id == manager_user.id
    assert api_client.post("/api/team/foundation-targets/change",payload)[0] == 409
    assert api_client.post("/api/team/foundation-targets/change",dict(payload,stageCode="W9"))[0] == 422
    assert api_client.post("/api/team/foundation-targets/change",dict(payload,dailyTarget=-1))[0] == 422
    assert api_client.post("/api/team/foundation-targets/change",dict(payload,effectiveFrom="2026-01-01"))[0] == 400
    assert api_client.get("/api/team/foundation-target-rules")[0] == 200
    api_client.login(employee_user.email,"test-password")
    assert api_client.post("/api/team/foundation-targets/change",payload)[0] == 403
    assert api_client.get("/api/team/foundation-target-rules")[0] == 403


def test_join_date_patch_rebuilds_training_and_departure(api_client,manager_user):
    user = user_for(manager_user)
    upload(manager_user,[chart(user)])
    api_client.login(manager_user.email,"test-password")
    status,body = api_client.patch(f"/api/users/{user.id}", {"join_date":"2026-04-01"})
    assert status == 200,body
    assert body["data"]["join_date"] == "2026-04-01"
    assert periods(user)["Training"].start_date == date(2026,4,1)
    status,body = api_client.delete(f"/api/users/{user.id}", {"last_working_day":"2026-04-25"})
    assert status == 200,body
    assert periods(user)["M1"].end_date == date(2026,4,25)
    assert "M2" not in periods(user)
