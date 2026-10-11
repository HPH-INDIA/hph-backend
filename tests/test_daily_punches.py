from datetime import date, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.extensions import db
from app.login_hours import daily_punches
from app.login_hours.models import LoginHoursUploadBatch
from app.roles.models import Role, RoleType
from app.features.models import Feature
from tests.test_login_hours import HEADERS, _workbook_base64
from tests.test_stage_read_access import person

TODAY=date(2026,10,11)

@pytest.fixture(autouse=True)
def today(monkeypatch):
    monkeypatch.setattr(daily_punches, 'business_today', lambda: TODAY)

@pytest.fixture
def roster(manager_user):
    lead=person(manager_user,'lead',manager_user.id)
    coder=person(manager_user,'employee',lead.id)
    omitted=person(manager_user,'employee',lead.id)
    future=person(manager_user,'employee',lead.id)
    future.join_date=TODAY+timedelta(days=1)
    inactive=person(manager_user,'employee',lead.id)
    inactive.is_active=False
    departed=person(manager_user,'employee',lead.id)
    departed.last_working_day=TODAY-timedelta(days=1)
    db.session.commit()
    return lead,coder,omitted,future,inactive,departed

def row(user, day=TODAY, first='09:00', entries=1, department='Coding'):
    name=f'{user.first_name} {user.last_name}' if not isinstance(user,str) else user
    return [day.isoformat(), 'P123', name, department, first, '17:00', '8h', '0h', '8h', entries, 1, 'Complete', 0]

def workbook(client, manager, rows):
    assert client.login(manager.email,'test-password')[0]==200
    result=client.post('/api/login-hours/uploads',dict(sourceFilename='daily.xlsx',fileBase64=_workbook_base64('Coding',1,rows)))
    assert result[0]==201,result
    return result[1]['data']

def report(client):
    status,body=client.get('/api/login-hours/today-punches')
    assert status==200,body
    return body['data']

def missing(result):
    return {u['id'] for u in result['missingUsers']}

def test_no_today_upload_means_no_roster_or_counts(api_client,manager_user,roster,monkeypatch):
    api_client.login(manager_user.email,'test-password')
    result=report(api_client)
    assert not result['available'] and result['missingUsers']==[] and result['totalUsers']==0
    workbook(api_client,manager_user,[row(roster[0],TODAY-timedelta(days=1))])
    assert not report(api_client)['available']
    workbook(api_client,manager_user,[row(roster[0])])
    assert report(api_client)['available']
    monkeypatch.setattr(daily_punches,'business_today',lambda:TODAY+timedelta(days=1))
    result=report(api_client)
    assert result['date']=='2026-10-12' and not result['available'] and result['missingUsers']==[]

def test_blank_and_omitted_punches_and_employment_filters(api_client,manager_user,roster):
    lead,coder,omitted,future,inactive,departed=roster
    workbook(api_client,manager_user,[row(lead),row(coder,first=None,entries=0)])
    result=report(api_client)
    assert result['available']
    assert coder.id in missing(result) and omitted.id in missing(result)
    assert not {lead.id,future.id,inactive.id,departed.id,manager_user.id} & missing(result)
    assert result['totalUsers']==result['punchedUsers']+len(result['missingUsers'])

def test_first_in_midnight_and_entry_count_are_punch_evidence(api_client,manager_user,roster):
    workbook(api_client,manager_user,[row(roster[0],first='00:00',entries=0),row(roster[1],first=None,entries=2)])
    assert not {roster[0].id,roster[1].id} & missing(report(api_client))

def test_today_rows_must_be_coding_and_valid(api_client,manager_user,roster):
    workbook(api_client,manager_user,[row(roster[0],department='RCM')])
    assert not report(api_client)['available']
    workbook(api_client,manager_user,[row('Unmatched Employee')])
    result=report(api_client)
    assert result['available'] and result['unmatchedCount']==1
    assert {p.id for p in roster[:3]}.issubset(missing(result))

def test_latest_today_sheet_replaces_old_punches_but_history_does_not(api_client,manager_user,roster):
    lead,coder=roster[:2]
    first=workbook(api_client,manager_user,[row(lead),row(coder)])
    assert lead.id not in missing(report(api_client))
    latest=workbook(api_client,manager_user,[row(coder)])
    assert latest['id']!=first['id']
    result=report(api_client)
    assert lead.id in missing(result) and coder.id not in missing(result)
    workbook(api_client,manager_user,[row(lead,TODAY-timedelta(days=1))])
    assert missing(report(api_client))==missing(result)

def test_chunks_publish_only_on_final_chunk_and_final_retry_is_safe(api_client,manager_user,roster):
    api_client.login(manager_user.email,'test-password')
    request=dict(sourceFilename='chunks.xlsx',sourceFormat='Employee-wise attendance',uploadId=str(uuid4()),
                 chunkIndex=0,headers=HEADERS,rows=[row(roster[0])],isFinalChunk=False)
    status,body=api_client.post('/api/login-hours/uploads/records',request)
    assert status==201 and body['data']['completedAt'] is None
    assert not report(api_client)['available']
    final={**request,'batchId':body['data']['id'],'chunkIndex':1,'rows':[row(roster[1])],'isFinalChunk':True}
    status,body=api_client.post('/api/login-hours/uploads/records',final)
    assert status==201 and body['data']['completedAt']
    result=report(api_client)
    assert result['available'] and not {roster[0].id,roster[1].id} & missing(result)
    assert api_client.post('/api/login-hours/uploads/records',final)[0]==201
    assert missing(report(api_client))==missing(result)
    assert api_client.post('/api/login-hours/uploads/records',{**final,'chunkIndex':2})[0]==409

def test_incomplete_later_upload_cannot_corrupt_previous_completed_report(api_client,manager_user,roster):
    workbook(api_client,manager_user,[row(roster[0])])
    result=report(api_client)
    request=dict(sourceFilename='partial.xlsx',sourceFormat='Employee-wise attendance',uploadId=str(uuid4()),
                 chunkIndex=0,headers=HEADERS,rows=[row(roster[0],first=None,entries=0)])
    assert api_client.post('/api/login-hours/uploads/records',request)[0]==201
    assert missing(report(api_client))==missing(result)
    assert api_client.post('/api/login-hours/uploads/records',{**request,'isFinalChunk':True})[0]==201
    assert roster[0].id in missing(report(api_client))

@pytest.mark.parametrize('kind',['lead','employee','admin','super_admin'])
def test_report_is_manager_only(api_client,manager_user,kind):
    user=person(manager_user,kind,manager_user.id)
    assert api_client.login(user.email,'read-stage-test')[0]==200
    assert api_client.get('/api/login-hours/today-punches')[0]==403

def test_manager_requires_login_hours_read_permission(api_client,manager_user):
    feature=Feature.query.filter_by(codename='login_hours').one()
    params={'role':manager_user.role_id,'feature':feature.id}
    old=db.session.execute(text('SELECT can_read,can_write FROM role_features WHERE role_id=:role AND feature_id=:feature'),params).one()
    db.session.execute(text('UPDATE role_features SET can_read=false,can_write=false WHERE role_id=:role AND feature_id=:feature'),params)
    db.session.commit()
    try:
        api_client.login(manager_user.email,'test-password')
        assert api_client.get('/api/login-hours/today-punches')[0]==403
    finally:
        db.session.execute(text('UPDATE role_features SET can_read=:read,can_write=:write WHERE role_id=:role AND feature_id=:feature'),
                           dict(params,read=old.can_read,write=old.can_write))
        db.session.commit()

def test_all_punched_returns_success_without_false_missing_users(api_client,manager_user,roster):
    workbook(api_client,manager_user,[row(roster[0])])
    original=report(api_client)
    batch=LoginHoursUploadBatch.query.one()
    batch.punch_user_ids_by_date={TODAY.isoformat():[roster[0].id,*missing(original)]}
    db.session.commit()
    result=report(api_client)
    assert result['available'] and result['missingUsers']==[] and result['punchedUsers']==result['totalUsers']

def test_same_user_correction_across_chunks_uses_last_observation(api_client,manager_user,roster):
    api_client.login(manager_user.email,'test-password')
    request=dict(sourceFilename='correction.xlsx',sourceFormat='Employee-wise attendance',uploadId=str(uuid4()),
                 chunkIndex=0,headers=HEADERS,rows=[row(roster[0])])
    status,body=api_client.post('/api/login-hours/uploads/records',request)
    assert status==201
    final={**request,'batchId':body['data']['id'],'chunkIndex':1,'rows':[row(roster[0],first=None,entries=0)],'isFinalChunk':True}
    assert api_client.post('/api/login-hours/uploads/records',final)[0]==201
    assert roster[0].id in missing(report(api_client))


def test_exact_database_name_wins_over_legacy_alias(api_client,manager_user,roster,monkeypatch):
    from app.login_hours.services import ATTENDANCE_NAME_ALIASES, _normalize
    user=roster[0]
    monkeypatch.setitem(ATTENDANCE_NAME_ALIASES,_normalize(f'{user.first_name} {user.last_name}'),'unmatchedlegacyspelling')
    batch=workbook(api_client,manager_user,[row(user)])
    assert batch['matchedCount']==1 and batch['unmatchedCount']==0
    assert user.id not in missing(report(api_client))
