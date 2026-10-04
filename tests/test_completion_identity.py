import datetime as dt

from app.extensions import db
from app.kairon.models import KaironChartRecord, KaironUploadBatch
from app.kairon.production import unique_completed_production
from app.reports.services import get_coding_dashboard, get_efficiency, _kairon_user_summaries
from test_reports import _get_or_create_user


DAY = dt.date(2026, 8, 31)


def make_batch(manager):
    batch = KaironUploadBatch(uploaded_by_id=manager.id, source_filename="completion-test.csv", status="completed")
    db.session.add(batch)
    db.session.flush()
    return batch


def add_record(batch, user, **overrides):
    values = dict(batch_id=batch.id, mbi_fingerprint="a" * 64, program="PVP", level="1LR", status="Completed", user_id=user.id,
                  coding_analyst_raw=f"{user.first_name} {user.last_name}", actions=1,
                  created_date=dt.date(2026, 8, 27), completed_date=DAY, practice="Practice A")
    values.update(overrides)
    record = KaironChartRecord(**values)
    db.session.add(record)
    db.session.flush()
    return record


def coder():
    return _get_or_create_user("completion-identity@example.com", "Completion", "Identity", "TEST-COMPLETION")


def test_creation_program_and_practice_do_not_split_completion(manager_user):
    user = coder()
    batch = make_batch(manager_user)
    first = add_record(batch, user)
    add_record(batch, user, created_date=DAY, program="FOUNDATION", practice="Practice B")
    db.session.commit()
    assert [r.id for r in KaironChartRecord.query.filter(unique_completed_production()).all()] == [first.id]
    efficiency = get_efficiency([user.id], DAY, DAY, include_daily=True)[user.id]
    assert efficiency["kairon_charts"] == 1
    assert efficiency["daily"][0]["kairon_charts"] == 1
    dashboard = get_coding_dashboard(DAY, DAY)
    card = next(row for row in dashboard if row["user_id"] == user.id)
    assert card["kairon"]["completed"] == 1
    assert _kairon_user_summaries([user.id], DAY, DAY)[user.id]["total"] == 1
    assert get_efficiency([user.id], DAY, DAY, program="FOUNDATION")[user.id]["kairon_charts"] == 0
    # Reporting deduplication never deletes either source record.
    assert KaironChartRecord.query.count() == 2


def test_level_date_and_beneficiary_remain_distinct_and_unknown_legacy_rows_survive(manager_user):
    user = coder()
    batch = make_batch(manager_user)
    add_record(batch, user)
    add_record(batch, user, level="2LR")
    add_record(batch, user, completed_date=DAY-dt.timedelta(days=1))
    add_record(batch, user, mbi_fingerprint="b" * 64)
    add_record(batch, user, mbi_fingerprint=None)
    add_record(batch, user, mbi_fingerprint=None)
    add_record(batch, user, status="On Hold", completed_date=None)
    db.session.commit()
    assert KaironChartRecord.query.filter(unique_completed_production()).count() == 6


def test_canonical_selection_is_global_and_excludes_superseded_batches(manager_user, employee_user):
    user = coder()
    old = make_batch(manager_user)
    add_record(old, employee_user)
    old.superseded_at = dt.datetime.now(dt.timezone.utc)
    current = make_batch(manager_user)
    first = add_record(current, user)
    add_record(current, employee_user, created_date=DAY)
    db.session.commit()
    ids = [r.id for r in KaironChartRecord.query.filter(unique_completed_production()).all()]
    assert ids == [first.id]
    result = get_efficiency([user.id, employee_user.id], DAY, DAY)
    assert result[user.id]["kairon_charts"] == 1
    assert result[employee_user.id]["kairon_charts"] == 0


def test_completed_report_counts_and_drilldown_agree(api_client, manager_user):
    user = coder()
    batch = make_batch(manager_user)
    first = add_record(batch, user)
    add_record(batch, user, created_date=DAY)
    db.session.commit()
    api_client.login(user.email, "test-password")
    status, body = api_client.get("/api/reports/kairon/completed-counts")
    assert status == 200, body
    assert body["data"]["items"] == [{"date": DAY.isoformat(), "count": 1}]
    status, body = api_client.get(f"/api/reports/kairon/completed-users?completedDate={DAY.isoformat()}")
    assert status == 200, body
    assert body["data"]["items"][0]["count"] == 1
    status, body = api_client.get(f"/api/reports/kairon/completed-records?completedDate={DAY.isoformat()}&userId={user.id}")
    assert status == 200, body
    assert body["data"]["total"] == 1
    assert body["data"]["items"][0]["id"] == first.id
