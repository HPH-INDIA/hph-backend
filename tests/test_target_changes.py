"""Explicit target-change scope recalculates snapshots without editing inputs."""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.cohorts import target_changes
from app.cohorts.models import StageTargetChange, StageTargetRule, UserStagePeriod
from app.extensions import db
from app.manual_daily_records.models import ManualDailyRecord
from app.manual_daily_records.productivity import snapshot_manual_productivity

TODAY = date(2026, 10, 8)


@pytest.fixture(autouse=True)
def frozen_day(monkeypatch):
    monkeypatch.setattr(target_changes, "business_today", lambda: TODAY)


def records_for(user):
    db.session.add_all([
        UserStagePeriod(user_id=user.id, stage_code="M1", start_date=TODAY-timedelta(days=10),
                        end_date=TODAY+timedelta(days=19), source="observed_first_activity"),
        UserStagePeriod(user_id=user.id, stage_code="M2", start_date=TODAY+timedelta(days=20),
                        end_date=None, source="calendar_offset"),
    ])
    records = [ManualDailyRecord(
        user_id=user.id, record_date=day, production_count=11, pvp_count=11, foundation_count=0,
        tech_issues_downtime_hours=1, no_inventory_idle_time_hours=0.5, leave_hours=0.5,
        meeting_engagement_hours=1.5,
        meetings=[{"type":"Huddle", "hours":0.5}, {"type":"Meeting", "hours":1}],
        status="approved", reviewed_at=None,
    ) for day in [TODAY-timedelta(days=1), TODAY, TODAY+timedelta(days=20)]]
    db.session.add_all(records)
    snapshot_manual_productivity(records)
    db.session.commit()
    return records


def submit(api_client, manager, scope="today", target=16, **extra):
    api_client.login(manager.email, "test-password")
    return api_client.post("/api/team/stage-targets/change", dict(
        stageCode="M1", dailyTarget=target, applyFrom=scope, **extra))


def test_today_preserves_earlier_and_other_stage_records(api_client, manager_user, employee_user):
    past, today, other = records_for(employee_user)
    status, body = submit(api_client, manager_user)
    assert status == 201, body
    assert body["data"]["effective_from"] == TODAY.isoformat()
    assert body["data"]["recalculatedRecords"] == 1
    db.session.expire_all()
    assert (past.daily_target, past.adjusted_cpd) == (7, Decimal("4.38"))
    assert (today.daily_target, today.adjusted_cpd) == (16, Decimal("10.00"))
    assert (other.daily_target, other.adjusted_cpd) == (14, Decimal("8.75"))
    assert today.production_count == 11 and today.status == "approved"
    assert today.meetings[0]["type"] == "Huddle"
    audit = StageTargetChange.query.one()
    assert audit.created_by_id == manager_user.id and audit.apply_from == "today"
    assert audit.previous_rules[0]["daily_target"] == 7


def test_program_start_replaces_history_future_rules_and_all_affected_snapshots(api_client, manager_user, employee_user):
    past, today, other = records_for(employee_user)
    assert submit(api_client, manager_user, target=10)[0] == 201
    target_changes.change_target(manager_user, "M1", 20, effective_from=TODAY+timedelta(days=2))
    status, body = submit(api_client, manager_user, "program_start", 24, reason="Correct original target")
    assert status == 201, body
    assert body["data"]["recalculatedRecords"] == 2
    db.session.expire_all()
    assert (past.daily_target, past.adjusted_cpd) == (24, Decimal("15.00"))
    assert (today.daily_target, today.adjusted_cpd) == (24, Decimal("15.00"))
    assert other.daily_target == 14
    rule = StageTargetRule.query.filter_by(stage_code="M1").one()
    assert rule.effective_from == date(1900,1,1) and rule.effective_to is None
    audit = StageTargetChange.query.order_by(StageTargetChange.id.desc()).first()
    assert [r["daily_target"] for r in audit.previous_rules] == [7,10,20]
    assert audit.reason == "Correct original target"


def test_same_day_change_can_be_saved_again(api_client, manager_user, employee_user):
    records_for(employee_user)
    assert submit(api_client, manager_user, target=12)[0] == 201
    status, body = submit(api_client, manager_user, target=8)
    assert status == 201, body
    rules = StageTargetRule.query.filter_by(stage_code="M1").order_by(StageTargetRule.effective_from).all()
    assert [(r.daily_target,r.effective_to) for r in rules] == [(7,TODAY),(8,None)]
    assert StageTargetChange.query.count() == 2


def test_failed_recalculation_rolls_back_rules_snapshots_and_audit(manager_user, employee_user, monkeypatch):
    past, today, _ = records_for(employee_user)
    def fail(records):
        records[0].adjusted_cpd = 999
        db.session.flush()
        raise RuntimeError("recalculation failed")
    monkeypatch.setattr(target_changes, "snapshot_manual_productivity", fail)
    with pytest.raises(RuntimeError):
        target_changes.change_target(manager_user,"M1",20,apply_from="program_start")
    db.session.expire_all()
    assert StageTargetRule.query.filter_by(stage_code="M1").one().daily_target == 7
    assert past.adjusted_cpd == today.adjusted_cpd == Decimal("4.38")
    assert StageTargetChange.query.count() == 0


@pytest.mark.parametrize("payload", [
    dict(stageCode="M1",dailyTarget=8),
    dict(stageCode="M1",dailyTarget=8,applyFrom="yesterday"),
    dict(stageCode="M1",dailyTarget=8,applyFrom="today",effectiveFrom="2026-10-08"),
    dict(stageCode="M1",dailyTarget=-1,applyFrom="today"),
    dict(stageCode="M1",dailyTarget=1.5,applyFrom="today"),
])
def test_invalid_confirmation_scope(api_client, manager_user, payload):
    api_client.login(manager_user.email, "test-password")
    assert api_client.post("/api/team/stage-targets/change",payload)[0] == 422
    assert StageTargetChange.query.count() == 0


def test_employee_cannot_apply_historical_targets(api_client, employee_user):
    api_client.login(employee_user.email, "test-password")
    assert api_client.post("/api/team/stage-targets/change", dict(
        stageCode="M1", dailyTarget=20, applyFrom="program_start"))[0] == 403
