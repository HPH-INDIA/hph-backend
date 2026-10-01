"""Range reporting retains daily source records and reporting-line scope."""
import datetime as dt

import pytest

from app.extensions import db
from app.features.models import Feature
from app.roles.models import role_features
from test_manual_lead_workflow import _login, _record, manual_team  # noqa: F401


START = dt.date(2026, 9, 28)
END = dt.date(2026, 9, 30)
RANGE_URL = "/api/reports/manual/team-range?fromDate=2026-09-28&toDate=2026-09-30"


@pytest.mark.parametrize("actor", ["manager", "lead"])
def test_range_groups_daily_records_with_inclusive_dates_and_separate_leads(api_client, manual_team, actor):
    users, _ = manual_team
    before = _record(users["coder"], START - dt.timedelta(days=1))
    last = _record(users["coder"], END, meeting_type="Training", status="approved")
    first = _record(users["coder"], START, meeting_type="PKT", status="rejected")
    after = _record(users["coder"], END + dt.timedelta(days=1))
    lead_record = _record(users["lead"], START, production_count=20, pvp_count=16, foundation_count=4)
    sibling_record = _record(users["sibling-coder"], START)
    _record(users["outside-coder"], START)
    _record(users["manager"], START)

    _login(api_client, users[actor])
    status, body = api_client.get(RANGE_URL)

    assert status == 200, body
    assert body["data"]["fromDate"] == START.isoformat()
    assert body["data"]["toDate"] == END.isoformat()
    teams = {team["lead"]["id"]: team for team in body["data"]["teams"]}
    expected_leads = {users["lead"].id, users["sibling-lead"].id} if actor == "manager" else {users["lead"].id}
    assert set(teams) == expected_leads
    team = teams[users["lead"].id]
    assert [record["id"] for record in team["leadRecords"]] == [lead_record.id]
    coders = {coder["user"]["id"]: coder for coder in team["coders"]}
    assert set(coders) == {users["coder"].id, users["missing"].id}
    assert coders[users["missing"].id]["records"] == []
    rows = coders[users["coder"].id]["records"]
    assert [record["id"] for record in rows] == [first.id, last.id]
    assert [record["meetingType"] for record in rows] == ["PKT", "Training"]
    assert [record["status"] for record in rows] == ["rejected", "approved"]
    all_records = [record for group in teams.values() for coder in group["coders"] for record in coder["records"]]
    assert not {before.id, after.id, lead_record.id}.intersection(record["id"] for record in all_records)
    # Lead production is exposed only through leadRecords, never counted again as coder production.
    assert sum(record["productionCount"] for record in team["leadRecords"]) == 20
    assert sum(record["productionCount"] for coder in team["coders"] for record in coder["records"]) == 24
    if actor == "manager":
        sibling = teams[users["sibling-lead"].id]
        assert sibling["leadRecords"] == []
        assert [record["id"] for coder in sibling["coders"] for record in coder["records"]] == [sibling_record.id]


def test_range_keeps_historical_members_and_trims_records_after_last_working_day(api_client, manual_team):
    users, make = manual_team
    lead = users["lead"]
    former = make("former", reports_to=lead, is_active=False, last_working_day=dt.date(2026, 9, 29))
    departed = make("departed", reports_to=lead, is_active=False, last_working_day=dt.date(2026, 9, 27))
    historical = make("historical", reports_to=lead, is_active=False)
    no_activity = make("no-activity", reports_to=lead, is_active=False)
    former_records = [_record(former, START), _record(former, dt.date(2026, 9, 29))]
    _record(former, END)
    _record(departed, START)
    historical_record = _record(historical, START)
    _record(no_activity, START - dt.timedelta(days=1))
    _login(api_client, users["manager"])

    status, body = api_client.get(RANGE_URL)

    assert status == 200, body
    team = next(team for team in body["data"]["teams"] if team["lead"]["id"] == lead.id)
    coders = {coder["user"]["id"]: coder for coder in team["coders"]}
    assert departed.id not in coders
    assert no_activity.id not in coders
    assert [record["id"] for record in coders[former.id]["records"]] == [record.id for record in former_records]
    assert [record["id"] for record in coders[historical.id]["records"]] == [historical_record.id]


def test_range_returns_all_daily_rows_without_pagination(api_client, manual_team):
    users, _ = manual_team
    for offset in range(35):
        _record(users["coder"], START + dt.timedelta(days=offset))
    _login(api_client, users["lead"])

    status, body = api_client.get("/api/reports/manual/team-range?fromDate=2026-09-28&toDate=2026-11-01")

    assert status == 200, body
    coder = next(coder for coder in body["data"]["teams"][0]["coders"] if coder["user"]["id"] == users["coder"].id)
    assert len(coder["records"]) == 35
    assert coder["records"][0]["date"] == "2026-09-28"
    assert coder["records"][-1]["date"] == "2026-11-01"


def test_single_day_range_matches_existing_daily_response(api_client, manual_team):
    users, _ = manual_team
    _record(users["lead"], START)
    _record(users["coder"], START)
    _login(api_client, users["manager"])
    status, day = api_client.get("/api/reports/manual/team-day?date=2026-09-28")
    assert status == 200, day
    status, window = api_client.get("/api/reports/manual/team-range?fromDate=2026-09-28&toDate=2026-09-28")
    assert status == 200, window
    converted = [
        {
            "lead": team["lead"],
            "leadRecord": next(iter(team["leadRecords"]), None),
            "coders": [{"user": coder["user"], "record": next(iter(coder["records"]), None)} for coder in team["coders"]],
        }
        for team in window["data"]["teams"]
    ]
    assert converted == day["data"]["teams"]


@pytest.mark.parametrize("query", [
    "", "?fromDate=2026-09-28", "?toDate=2026-09-30",
    "?fromDate=invalid&toDate=2026-09-30",
    "?fromDate=2026-09-30&toDate=2026-09-28",
])
def test_range_requires_valid_ordered_dates(api_client, manual_team, query):
    users, _ = manual_team
    _login(api_client, users["manager"])
    status, body = api_client.get("/api/reports/manual/team-range" + query)
    assert status == 422, body


def test_employee_cannot_read_team_range(api_client, manual_team):
    users, _ = manual_team
    _login(api_client, users["coder"])
    status, body = api_client.get(RANGE_URL)
    assert status == 403, body


@pytest.mark.parametrize("actor", ["manager", "lead"])
def test_range_requires_reports_read_but_not_write(api_client, manual_team, actor):
    users, _ = manual_team
    user = users[actor]
    feature = Feature.query.filter_by(codename="reports").one()
    where = (role_features.c.role_id == user.role_id) & (role_features.c.feature_id == feature.id)
    original = db.session.execute(db.select(role_features.c.can_read, role_features.c.can_write).where(where)).one()
    try:
        db.session.execute(role_features.update().where(where).values(can_read=True, can_write=False))
        db.session.commit()
        _login(api_client, user)
        status, body = api_client.get(RANGE_URL)
        assert status == 200, body
        db.session.execute(role_features.update().where(where).values(can_read=False, can_write=False))
        db.session.commit()
        status, body = api_client.get(RANGE_URL)
        assert status == 403, body
    finally:
        db.session.execute(role_features.update().where(where).values(can_read=original.can_read, can_write=original.can_write))
        db.session.commit()
