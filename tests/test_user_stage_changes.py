from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.cohorts import stage_changes, stage_refresh, target_changes
from app.cohorts.models import UserStageChange, UserStageEvidence, UserStagePeriod
from app.cohorts.progression import main_periods, foundation_progress, period_on
from app.extensions import db
from app.manual_daily_records.models import ManualDailyRecord
from app.manual_daily_records.productivity import snapshot_manual_productivity
from tests.test_user_progression import user_for, upload, chart

TODAY = date(2026, 10, 11)


@pytest.fixture(autouse=True)
def freeze_day(monkeypatch):
    for module in (stage_changes, stage_refresh, target_changes):
        monkeypatch.setattr(module, 'business_today', lambda: TODAY)


@pytest.fixture
def stage_user(manager_user):
    user = user_for(manager_user)
    db.session.add(UserStageEvidence(user_id=user.id, first_foundation_completed=date(2026, 9, 1),
                                    first_pvp_completed=date(2026, 9, 15)))
    stage_refresh.rebuild_main_periods(user)
    db.session.commit()
    return user


def record(user, day, pvp=11, foundation=0):
    row = ManualDailyRecord(user_id=user.id, record_date=day, pvp_count=pvp, foundation_count=foundation,
                            production_count=pvp+foundation, tech_issues_downtime_hours=2,
                            no_inventory_idle_time_hours=0, leave_hours=0, meeting_engagement_hours=0,
                            status='approved', reviewed_at=None)
    db.session.add(row)
    snapshot_manual_productivity([row])
    db.session.commit()
    return row


def submit(client, manager, user, program='main', stage='M1', day=TODAY, reason='Refresher training'):
    client.login(manager.email, 'test-password')
    return client.post(f'/api/team/users/{user.id}/stage-changes', dict(
        program=program, stageCode=stage, effectiveFrom=day.isoformat(), reason=reason))


def main_path(user):
    return [dict(stage_code=p.stage_code, start_date=p.start_date, end_date=p.end_date)
            for p in UserStagePeriod.query.filter_by(user_id=user.id).order_by(UserStagePeriod.start_date)]


@pytest.mark.parametrize('stage,days', [('M1',120),('M2',90),('M3',60),('M4',30)])
def test_main_restart_advances_to_steady_state(manager_user, stage_user, stage, days):
    change = stage_changes.change_user_stage(manager_user, stage_user.id, 'main', stage, TODAY, 'Restart ramp')
    path = main_path(stage_user)
    assert period_on(path, TODAY)['stage_code'] == stage
    assert period_on(path, TODAY+timedelta(days=29))['stage_code'] == stage
    assert period_on(path, TODAY+timedelta(days=days))['stage_code'] == 'Steady State'
    assert period_on(path, TODAY-timedelta(days=1))['stage_code'] == 'Steady State'
    assert change.recalculated_records == 0
    assert len([p for p in path if p['stage_code']=='Steady State']) == 2


@pytest.mark.parametrize('stage,days', [('W1',28),('W2',21),('W3',14),('W4',7)])
def test_foundation_restart_advances_independently(manager_user, stage_user, stage, days):
    original = main_path(stage_user)
    event = stage_changes.change_user_stage(manager_user, stage_user.id, 'foundation', stage, TODAY, 'Foundation refresher')
    ev = db.session.get(UserStageEvidence, stage_user.id)
    result = foundation_progress(ev.first_pvp_completed, ev.first_foundation_completed, TODAY,
                                 changes=[event])
    assert result['current_stage'] == stage
    assert period_on(result['periods'],TODAY+timedelta(days=6))['stage_code'] == stage
    assert period_on(result['periods'],TODAY+timedelta(days=days))['stage_code'] == 'Steady State'
    assert main_path(stage_user) == original


def test_main_change_recalculates_only_this_users_effective_dates(api_client, manager_user, stage_user):
    prior = record(stage_user,TODAY-timedelta(days=1))
    current = record(stage_user,TODAY)
    next_stage = record(stage_user,TODAY+timedelta(days=30))
    other_user=user_for(manager_user)
    stage_refresh.rebuild_main_periods(other_user)
    other=record(other_user,TODAY)
    status,body=submit(api_client,manager_user,stage_user,stage='M2')
    assert status==201,body
    assert body['data']['recalculatedRecords']==2
    db.session.expire_all()
    assert prior.pvp_daily_target==30 and prior.adjusted_cpd==Decimal('22.50')
    assert current.pvp_daily_target==14 and current.adjusted_cpd==Decimal('10.50')
    assert next_stage.pvp_daily_target==20 and next_stage.adjusted_cpd==Decimal('15.00')
    assert other.adjusted_cpd==Decimal('22.50')
    assert current.production_count==11 and current.status=='approved'
    audit=UserStageChange.query.one()
    assert audit.created_by_id==manager_user.id and audit.reason=='Refresher training'


def test_foundation_restart_changes_combined_target_and_rule_edits_follow_override(api_client,manager_user,stage_user):
    prior=record(stage_user,TODAY-timedelta(days=1),pvp=15,foundation=3)
    current=record(stage_user,TODAY,pvp=15,foundation=3)
    status,body=submit(api_client,manager_user,stage_user,program='foundation',stage='W1')
    assert status==201,body
    db.session.expire_all()
    assert prior.adjusted_cpd==Decimal('22.50')
    assert current.pvp_daily_target==30 and current.foundation_daily_target==7
    assert current.daily_target==Decimal('19.384615') and current.adjusted_cpd==Decimal('14.54')
    target_changes.change_target(manager_user,'W1',14,foundation=True,apply_from='today')
    db.session.expire_all()
    assert current.adjusted_cpd==Decimal('18.90')  # mixed target 25.2 at six hours
    assert prior.adjusted_cpd==Decimal('22.50')


def test_repeated_changes_and_reset_preserve_history(manager_user,stage_user):
    start=TODAY-timedelta(days=10)
    stage_changes.change_user_stage(manager_user,stage_user.id,'main','M1',start,'Restart')
    stage_changes.change_user_stage(manager_user,stage_user.id,'main','M3',start,'Correct same-day choice')
    stage_changes.change_user_stage(manager_user,stage_user.id,'main','M2',TODAY,'Second restart')
    assert period_on(main_path(stage_user),start)['stage_code']=='M3'
    assert period_on(main_path(stage_user),TODAY)['stage_code']=='M2'
    stage_changes.change_user_stage(manager_user,stage_user.id,'main',None,TODAY,'Restore joining timeline')
    assert period_on(main_path(stage_user),TODAY)['stage_code']=='Steady State'
    assert period_on(main_path(stage_user),TODAY-timedelta(days=1))['stage_code']=='M3'
    assert UserStageChange.query.count()==4


def test_foundation_reset_returns_to_original_completion_timeline(manager_user,stage_user):
    stage_changes.change_user_stage(manager_user,stage_user.id,'foundation','W2',TODAY,'Restart')
    stage_changes.change_user_stage(manager_user,stage_user.id,'foundation',None,TODAY,'Restore Foundation timeline')
    path=foundation_progress(None,date(2026,9,1),TODAY,
                             changes=UserStageChange.query.order_by(UserStageChange.id).all())
    assert path['current_stage']=='Steady State'


def test_kairon_corrections_do_not_erase_manager_restarts(manager_user,stage_user):
    stage_changes.change_user_stage(manager_user,stage_user.id,'main','M1',TODAY,'Restart')
    stage_changes.change_user_stage(manager_user,stage_user.id,'foundation','W1',TODAY,'Restart Foundation')
    upload(manager_user,[chart(stage_user,completed_date=date(2026,4,25),program='PVP')])
    assert period_on(main_path(stage_user),TODAY)['stage_code']=='M1'
    assert UserStageChange.query.filter_by(user_id=stage_user.id).count()==2
    # Foundation evidence disappears after correction, but an explicit manager
    # restart remains intentional until restored to automatic progression.
    ev=db.session.get(UserStageEvidence,stage_user.id)
    assert ev.first_foundation_completed is None
    assert foundation_progress(ev.first_pvp_completed,None,TODAY,
        changes=UserStageChange.query.filter_by(program='foundation').all())['current_stage']=='W1'


def test_failed_recalculation_rolls_back_everything(manager_user,stage_user,monkeypatch):
    row=record(stage_user,TODAY)
    original=main_path(stage_user)
    def fail(rows):
        rows[0].adjusted_cpd=999
        db.session.flush()
        raise RuntimeError('recalculation failed')
    monkeypatch.setattr(stage_changes,'snapshot_manual_productivity',fail)
    with pytest.raises(RuntimeError):
        stage_changes.change_user_stage(manager_user,stage_user.id,'main','M1',TODAY,'Restart')
    db.session.expire_all()
    assert main_path(stage_user)==original and row.adjusted_cpd==Decimal('22.50')
    assert UserStageChange.query.count()==0


@pytest.mark.parametrize('payload', [
    dict(program='main',stageCode='W1',reason='Wrong program'),
    dict(program='foundation',stageCode='M1',reason='Wrong program'),
    dict(program='main',stageCode='Training',reason='Unsupported restart'),
    dict(program='main',stageCode='M1',reason='   '),
])
def test_invalid_choices_rejected(api_client,manager_user,stage_user,payload):
    api_client.login(manager_user.email,'test-password')
    assert api_client.post(f'/api/team/users/{stage_user.id}/stage-changes',
        dict(payload,effectiveFrom=TODAY.isoformat()))[0]==422
    assert UserStageChange.query.count()==0


@pytest.mark.parametrize('day', [date(2026,4,5),TODAY+timedelta(days=1)])
def test_dates_outside_employment_or_future_rejected(api_client,manager_user,stage_user,day):
    assert submit(api_client,manager_user,stage_user,day=day)[0]==400


def test_foundation_cannot_restart_before_completion(api_client,manager_user,stage_user):
    assert submit(api_client,manager_user,stage_user,program='foundation',stage='W1',day=date(2026,8,31))[0]==400
    db.session.get(UserStageEvidence,stage_user.id).first_foundation_completed=None
    db.session.commit()
    assert submit(api_client,manager_user,stage_user,program='foundation',stage='W1')[0]==400


def test_employee_and_outside_team_cannot_change_stages(api_client,manager_user,employee_user,stage_user):
    api_client.login(employee_user.email,'test-password')
    assert api_client.post(f'/api/team/users/{stage_user.id}/stage-changes',dict(
        program='main',stageCode='M1',effectiveFrom=TODAY.isoformat(),reason='Forbidden'))[0]==403
    stage_user.reports_to_id=None
    db.session.commit()
    assert submit(api_client,manager_user,stage_user)[0]==403
    assert UserStageChange.query.count()==0


def test_former_user_capped_and_recalculates_only_working_dates(api_client,manager_user,stage_user):
    prior=record(stage_user,TODAY-timedelta(days=2))
    after=record(stage_user,TODAY)
    stage_user.is_active=False
    stage_user.last_working_day=TODAY-timedelta(days=1)
    stage_refresh.rebuild_main_periods(stage_user)
    db.session.commit()
    assert submit(api_client,manager_user,stage_user)[0]==400
    status,body=submit(api_client,manager_user,stage_user,day=TODAY-timedelta(days=2))
    assert status==201,body
    db.session.expire_all()
    assert prior.adjusted_cpd==Decimal('5.25') and after.adjusted_cpd==Decimal('22.50')
    assert period_on(main_path(stage_user),TODAY) is None


def test_database_prevents_overlapping_stage_periods(stage_user):
    db.session.add(UserStagePeriod(user_id=stage_user.id,stage_code='M1',start_date=TODAY,source='manual_override'))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_team_overview_exposes_restart_and_history(api_client,manager_user,stage_user):
    assert submit(api_client,manager_user,stage_user,stage='M2')[0]==201
    status,body=api_client.get('/api/team/coders?pageSize=100')
    assert status==200,body
    row=next(row for row in body['data']['items'] if row['coder']['id']==stage_user.id)
    assert row['currentStage']=='M2'
    assert row['foundation']['currentStage']=='Steady State'
    assert row['stageChanges'][0]['stageCode']=='M2'
    assert row['stageChanges'][0]['reason']=='Refresher training'
