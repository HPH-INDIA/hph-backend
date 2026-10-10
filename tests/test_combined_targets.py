from datetime import timedelta
from decimal import Decimal

import pytest

from app.cohorts.models import FoundationTargetRule, StageTargetRule, UserStageEvidence
from app.cohorts import target_changes
from app.extensions import db
from app.manual_daily_records.productivity import combined_daily_target, snapshot_manual_productivity
from app.reports.services import get_efficiency, get_monthly_goal
from tests.test_target_changes import TODAY, records_for


@pytest.mark.parametrize('pvp,foundation,main_rate,foundation_rate,expected', [
    (30,0,30,7,30), (0,7,30,7,7), (15,3,30,7,Decimal('19.384615')),
    (15,7,30,14,22), (30,0,30,None,30), (0,10,30,None,30),
    (0,0,30,7,None), (0,0,30,30,30), (0,0,30,None,30),
    (1,1,0,7,None), (1,1,30,0,None), (1,0,0,7,0), (0,1,30,0,0),
])
def test_actual_mix_capacity(pvp,foundation,main_rate,foundation_rate,expected):
    assert combined_daily_target(pvp,foundation,main_rate,foundation_rate) == expected


def mixed_records(user):
    StageTargetRule.query.filter_by(stage_code='M1').one().daily_target = 30
    db.session.add(UserStageEvidence(user_id=user.id, first_completed=TODAY-timedelta(days=10),
                                    first_pvp_completed=TODAY-timedelta(days=10),
                                    first_foundation_completed=TODAY-timedelta(days=1)))
    records=records_for(user)
    for record in records:
        record.pvp_count=15
        record.foundation_count=3
        record.production_count=18
    snapshot_manual_productivity(records)
    db.session.commit()
    return records


@pytest.mark.parametrize('scope,previous', [('today',Decimal('12.12')),('program_start',Decimal('15.75'))])
def test_foundation_edit_recalculates_combined_target_in_scope(api_client,manager_user,employee_user,monkeypatch,scope,previous):
    monkeypatch.setattr(target_changes,'business_today',lambda:TODAY)
    past,today,other=mixed_records(employee_user)
    assert today.daily_target == Decimal('19.384615') and today.adjusted_cpd == Decimal('12.12')
    before_other=other.adjusted_cpd
    api_client.login(manager_user.email,'test-password')
    status,body=api_client.post('/api/team/foundation-targets/change',dict(stageCode='W1',dailyTarget=14,applyFrom=scope))
    assert status==201,body
    assert body['data']['recalculatedRecords']==(1 if scope=='today' else 2)
    db.session.expire_all()
    assert past.adjusted_cpd==previous
    assert today.daily_target==Decimal('25.200000') and today.adjusted_cpd==Decimal('15.75')
    assert today.pvp_daily_target==30 and today.foundation_daily_target==14
    assert today.production_count==18 and today.status=='approved'
    assert other.adjusted_cpd==before_other
    daily=get_efficiency([employee_user.id],TODAY,TODAY,include_daily=True)[employee_user.id]['daily'][0]
    assert daily['daily_target']==Decimal('25.200000')
    assert daily['adjusted_cpd']==Decimal('15.75')


def test_changed_main_rate_also_recalculates_mixed_foundation_day(manager_user,employee_user,monkeypatch):
    monkeypatch.setattr(target_changes,'business_today',lambda:TODAY)
    past,today,_=mixed_records(employee_user)
    target_changes.change_target(manager_user,'M1',21,apply_from='today')
    db.session.expire_all()
    assert past.adjusted_cpd==Decimal('12.12')
    assert today.daily_target==Decimal('15.750000')
    assert today.adjusted_cpd==Decimal('9.84')


@pytest.mark.parametrize('order',['foundation_first','same_day'])
def test_ineligible_foundation_does_not_use_weekly_target(employee_user,order):
    records=mixed_records(employee_user)
    ev=db.session.get(UserStageEvidence,employee_user.id)
    ev.first_pvp_completed=ev.first_foundation_completed+(timedelta(days=1) if order=='foundation_first' else timedelta(0))
    snapshot_manual_productivity(records)
    db.session.commit()
    assert records[0].foundation_daily_target is None
    assert records[0].daily_target==30 and records[0].adjusted_cpd==Decimal('18.75')


def test_missing_mix_does_not_invent_combined_target(employee_user):
    records=mixed_records(employee_user)
    today=records[1]
    today.pvp_count=today.foundation_count=today.production_count=0
    snapshot_manual_productivity([today])
    db.session.commit()
    assert today.daily_target is None and today.adjusted_cpd is None
    assert today.pvp_daily_target==30 and today.foundation_daily_target==7


def test_former_employee_history_is_recalculated_until_last_day(manager_user,employee_user,monkeypatch):
    monkeypatch.setattr(target_changes,'business_today',lambda:TODAY)
    past,today,_=mixed_records(employee_user)
    employee_user.last_working_day=TODAY-timedelta(days=1)
    employee_user.is_active=False
    db.session.commit()
    try:
        target_changes.change_target(manager_user,'W1',14,foundation=True,apply_from='program_start')
        db.session.expire_all()
        assert past.adjusted_cpd==Decimal('15.75')
        assert today.adjusted_cpd==Decimal('12.12')
    finally:
        employee_user.last_working_day=None
        employee_user.is_active=True
        db.session.commit()


def test_monthly_goal_uses_recalculated_combined_snapshots(manager_user,employee_user,monkeypatch):
    monkeypatch.setattr(target_changes,'business_today',lambda:TODAY)
    mixed_records(employee_user)
    before=get_monthly_goal(employee_user,'2026-10')['adjusted_target_charts']
    target_changes.change_target(manager_user,'W1',14,foundation=True,apply_from='program_start')
    after=get_monthly_goal(employee_user,'2026-10')['adjusted_target_charts']
    assert after-before==Decimal('7.26')  # two workdays: 15.75 - 12.12 each


def test_kairon_and_manual_mixes_use_their_own_capacity(manager_user,employee_user):
    from app.kairon.models import KaironUploadBatch,KaironChartRecord
    mixed_records(employee_user)
    batch=KaironUploadBatch(uploaded_by_id=manager_user.id,status='completed',as_of_date=TODAY)
    db.session.add(batch)
    db.session.flush()
    # Manual has 15 PVP + 3 Foundation; Kairon independently has 3 Foundation.
    for _ in range(3):
        db.session.add(KaironChartRecord(batch_id=batch.id,program='Foundation',level='1LR',status='Completed',
                                       user_id=employee_user.id,coding_analyst_raw='Test Analyst',
                                       created_date=TODAY,completed_date=TODAY))
    db.session.commit()
    daily=get_efficiency([employee_user.id],TODAY,TODAY,include_daily=True)[employee_user.id]['daily'][0]
    assert daily['daily_target']==Decimal('19.384615')
    assert daily['kairon_efficiency_percent']==Decimal('76.1') # 3 / (7 * 4.5 / 8), operational exclusions
    foundation=get_efficiency([employee_user.id],TODAY,TODAY,include_daily=True,program='FOUNDATION')[employee_user.id]['daily'][0]
    assert foundation['daily_target']==7
    assert foundation['manual_efficiency_percent']==foundation['kairon_efficiency_percent']
