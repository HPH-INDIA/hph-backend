from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.cohorts.models import UserStageEvidence, UserStageChange
from app.cohorts.stage_refresh import rebuild_main_periods
from app.encryption.passwords import hash_password
from app.extensions import db
from app.features.models import Feature
from app.roles.models import Role, RoleType
from app.users.models import Project, User


def person(manager, kind, reports_to=None, project_id=None):
    token = uuid4().hex
    role = Role.query.join(RoleType).filter(RoleType.code == kind).first()
    user = User(email=f'{token}@example.test', emp_id=token, first_name=kind, last_name=token,
                role_id=role.id, project_id=project_id or manager.project_id,
                reports_to_id=reports_to, password_hash=hash_password('read-stage-test'),
                first_login=False, is_active=True, join_date=date(2026, 4, 6))
    db.session.add(user)
    db.session.flush()
    if kind in ('lead', 'employee'):
        db.session.add(UserStageEvidence(user_id=user.id, first_foundation_completed=date(2026, 9, 1)))
        rebuild_main_periods(user)
    db.session.commit()
    return user


@pytest.fixture
def people(manager_user):
    lead = person(manager_user, 'lead', manager_user.id)
    coder = person(manager_user, 'employee', lead.id)
    other_lead = person(manager_user, 'lead', manager_user.id)
    other_coder = person(manager_user, 'employee', other_lead.id)
    return lead, coder, other_lead, other_coder


def read(client, user, query=''):
    status, body = client.login(user.email, 'read-stage-test')
    assert status == 200, body
    return client.get('/api/team/coders' + query)


def ids(body):
    return {row['coder']['id'] for row in body['data']['items']}


def test_lead_can_read_only_self_and_direct_coders(api_client, people):
    lead, coder, other_lead, other_coder = people
    status, body = read(api_client, lead)
    assert status == 200, body
    assert ids(body) == {lead.id, coder.id}
    assert body['data']['total'] == 2
    for row in body['data']['items']:
        assert row['currentStage'] == 'Steady State'
        assert row['dailyTarget'] == 30
        assert row['foundation']['currentStage'] == 'Steady State'
        assert row['periods']
    status, body = api_client.get(f'/api/team/coders?leadId={other_lead.id}')
    assert status == 400
    status, body = api_client.get(f'/api/team/coders?leadId={lead.id}&pageSize=1')
    assert status == 200 and body['data']['total'] == 2 and len(ids(body)) == 1


def test_coder_can_read_only_self_and_filters_cannot_expand_scope(api_client, people):
    lead, coder, other_lead, other_coder = people
    status, body = read(api_client, coder)
    assert status == 200 and ids(body) == {coder.id}
    status, body = api_client.get(f'/api/team/coders?leadId={lead.id}')
    assert status == 400
    status, body = api_client.get('/api/team/coders?cohortId=999999&stageCode=Steady%20State')
    assert status == 200 and ids(body) == set()
    status, body = api_client.get('/api/team/coders?stageCode=M1')
    assert status == 200 and ids(body) == set()


@pytest.mark.parametrize('index', [0, 1])
def test_readers_have_no_admin_access_even_with_accidental_write_grant(api_client, people, index):
    user = people[index]
    admin_feature = Feature.query.filter_by(codename='user_management').one()
    prior = db.session.execute(text('SELECT can_read,can_write FROM role_features WHERE role_id=:role AND feature_id=:feature'),
                               {'role': user.role_id, 'feature': admin_feature.id}).first()
    db.session.execute(text('''INSERT INTO role_features(role_id,feature_id,can_read,can_write)
        VALUES (:role,:feature,true,true) ON CONFLICT(role_id,feature_id) DO UPDATE SET can_read=true,can_write=true'''),
        {'role': user.role_id, 'feature': admin_feature.id})
    db.session.commit()
    try:
        assert read(api_client, user)[0] == 200
        for path in ['/api/team/cohorts', '/api/team/cohorts/eligible-members', '/api/team/foundation-target-rules']:
            assert api_client.get(path)[0] == 403
        for path in [f'/api/team/users/{user.id}/stage-changes', '/api/team/stage-targets/change',
                     '/api/team/foundation-targets/change', '/api/team/cohorts']:
            assert api_client.post(path, {})[0] == 403, path
        assert UserStageChange.query.count() == 0
    finally:
        if prior:
            db.session.execute(text('UPDATE role_features SET can_read=:read,can_write=:write WHERE role_id=:role AND feature_id=:feature'),
                {'read': prior.can_read, 'write': prior.can_write, 'role': user.role_id, 'feature': admin_feature.id})
        else:
            db.session.execute(text('DELETE FROM role_features WHERE role_id=:role AND feature_id=:feature'),
                {'role': user.role_id, 'feature': admin_feature.id})
        db.session.commit()


def test_read_feature_can_be_revoked(api_client, people):
    user = people[1]
    feature = Feature.query.filter_by(codename='stage_view').one()
    db.session.execute(text('UPDATE role_features SET can_read=false,can_write=false WHERE role_id=:role AND feature_id=:feature'),
                       {'role': user.role_id, 'feature': feature.id})
    db.session.commit()
    try:
        assert read(api_client, user)[0] == 403
    finally:
        db.session.execute(text('UPDATE role_features SET can_read=true,can_write=false WHERE role_id=:role AND feature_id=:feature'),
                           {'role': user.role_id, 'feature': feature.id})
        db.session.commit()


def test_readers_cannot_view_other_projects(api_client, people, manager_user):
    lead, coder, _, _ = people
    project = Project(name='READ-STAGE-' + uuid4().hex)
    db.session.add(project)
    db.session.flush()
    outsider = person(manager_user, 'employee', lead.id, project.id)
    status, body = read(api_client, lead)
    assert status == 200 and ids(body) == {lead.id, coder.id}
    assert read(api_client, outsider)[0] == 403


def test_manager_stage_access_keeps_existing_team_scope(api_client, people, manager_user):
    assert api_client.login(manager_user.email, 'test-password')[0] == 200
    for lead, coder in [(people[0], people[1]), (people[2], people[3])]:
        status, body = api_client.get(f'/api/team/coders?leadId={lead.id}')
        assert status == 200 and ids(body) == {lead.id, coder.id}


def test_stage_read_migration_grants_no_write_access():
    permissions = db.session.execute(text('''SELECT rt.code,rf.can_read,rf.can_write FROM role_features rf
        JOIN features f ON f.id=rf.feature_id JOIN roles r ON r.id=rf.role_id
        JOIN role_types rt ON rt.id=r.role_type_id WHERE f.codename='stage_view' ''' )).all()
    assert permissions
    assert {'lead', 'employee'} == {p.code for p in permissions}
    assert all(p.can_read and not p.can_write for p in permissions)
