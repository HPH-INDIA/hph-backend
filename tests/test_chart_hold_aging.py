"""Age boundaries, authorized aggregates, and sorting across result pages."""
import datetime as dt

import pytest

from app.extensions import db
from app.kairon.models import KaironChartRecord
from tests.test_chart_holds import inventory, fetch  # noqa: F401


@pytest.fixture
def aged_inventory(inventory):
    # Make unrelated, completed, and superseded records tempting priority matches.
    KaironChartRecord.query.update({KaironChartRecord.age_days: 999})
    rows = KaironChartRecord.query.filter_by(user_id=inventory.coder.id, status="On Hold", batch_id=inventory.first.batch_id).order_by(KaironChartRecord.id).all()
    for record, age in zip(rows, [0, 10, 11, 20, 21]):
        record.age_days = age
    for age in [30, 31, 60, None, -1]:
        db.session.add(KaironChartRecord(batch_id=inventory.first.batch_id, user_id=inventory.coder.id,
                       program="PVP", level="1LR", status="On Hold", coding_analyst_raw="Hold coder",
                       created_date=dt.date(2026, 9, 15), practice="North", age_days=age, actions=1))
    db.session.commit()
    return inventory


def test_age_groups_have_exclusive_boundaries_and_unknowns(api_client, aged_inventory):
    assert api_client.login(aged_inventory.coder.email, "test-password")[0] == 200
    summary = fetch(api_client, "holds-summary")
    assert summary["ageBuckets"] == dict(on_track=2, monitor=2, attention=2, priority=2, unknown=2)
    assert sum(summary["ageBuckets"].values()) == summary["total"] == 10
    for bucket, ages in {"on_track": [0, 10], "monitor": [11, 20], "attention": [21, 30], "priority": [31, 60], "unknown": [None, -1]}.items():
        query = f"?ageBucket={bucket}"
        records = fetch(api_client, "holds", query)
        filtered_summary = fetch(api_client, "holds-summary", query)
        assert {row["age"] for row in records["items"]} == set(ages)
        assert records["total"] == filtered_summary["total"] == 2
        assert sum(user["count"] for user in filtered_summary["users"]) == 2
        assert filtered_summary["ageBuckets"] == summary["ageBuckets"]


def test_priority_inventory_remains_scoped_and_combines_filters(api_client, aged_inventory):
    inv = aged_inventory
    assert api_client.login(inv.manager.email, "test-password")[0] == 200
    query = f"?ageBucket=priority&leadId={inv.lead.id}&view=coders&userId={inv.coder.id}&practice=North&createdFrom=2026-09-15&createdTo=2026-09-15"
    records = fetch(api_client, "holds", query)
    summary = fetch(api_client, "holds-summary", query)
    assert records["total"] == summary["total"] == summary["ageBuckets"]["priority"] == 2
    assert {row["age"] for row in records["items"]} == {31, 60}
    all_priority = fetch(api_client, "holds", "?ageBucket=priority&sortBy=age&sortDirection=desc")
    assert all_priority["total"] == 6
    assert inv.outsider.id not in {row["userId"] for row in all_priority["items"]}
    assert {row["status"] for row in all_priority["items"]} == {"On Hold"}
    assert api_client.get(f"/api/reports/kairon/holds?ageBucket=priority&userId={inv.outsider.id}")[0] == 404


@pytest.mark.parametrize("sort_by", ["id", "user", "program", "created", "practice", "lastAction", "age", "tat"])
@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_every_column_sorts_before_pagination_with_nulls_last(api_client, inventory, sort_by, direction):
    inv = inventory
    inv.coder.first_name, inv.coder.last_name = "Zoe", "Alpha"
    inv.lead.first_name, inv.lead.last_name = "Aaron", "Zulu"
    members = [inv.coder, inv.coder2, inv.lead, inv.lead2, inv.inactive]
    names = {member.id: f"{member.first_name} {member.last_name}".strip().lower() for member in members}
    rows = KaironChartRecord.query.filter(KaironChartRecord.user_id.in_(names), KaironChartRecord.batch_id == inv.first.batch_id, KaironChartRecord.status == "On Hold").order_by(KaironChartRecord.id).all()
    for index, row in enumerate(rows):
        row.program = ["PVP", "Foundation"][index % 2]
        row.level = ["2LR", "1LR", "3LR"][index % 3]
        row.practice = ["zebra", "Alpha", None][index % 3]
        row.last_action = ["Review", "awaiting", None][index % 3]
        row.actions = index % 2
        row.age_days = [None, 40, 5][index % 3]
        row.tat_days = [8, None, 2][index % 3]
        row.coding_analyst_raw = "Different imported name"
    db.session.commit()
    def key(row):
        return {"id": (row.id,), "user": (names[row.user_id],), "program": (row.program.lower(), row.level),
                "created": (row.created_date,), "practice": (row.practice.lower() if row.practice else None,),
                "lastAction": (row.last_action.lower() if row.last_action else None, row.actions),
                "age": (row.age_days,), "tat": (row.tat_days,)}[sort_by]
    base = sorted(rows, key=lambda row: row.id, reverse=True)
    known = [row for row in base if key(row)[0] is not None]
    missing = [row for row in base if key(row)[0] is None]
    expected = sorted(known, key=key, reverse=direction == "desc")
    # Last action uses the visible action count as its secondary key even if text is missing.
    if sort_by == "lastAction":
        missing.sort(key=lambda row: row.actions, reverse=direction == "desc")
    expected += missing
    assert api_client.login(inv.manager.email, "test-password")[0] == 200
    query = f"?sortBy={sort_by}&sortDirection={direction}&pageSize=2"
    first = fetch(api_client, "holds", query)
    actual = first["items"]
    for page in range(2, first["totalPages"] + 1):
        actual.extend(fetch(api_client, "holds", query + f"&page={page}")["items"])
    assert [row["id"] for row in actual] == [row.id for row in expected]


@pytest.mark.parametrize("query", ["ageBucket=invalid", "sortBy=user;DROP TABLE users", "sortDirection=invalid"])
def test_sort_and_age_parameters_are_validated(api_client, inventory, query):
    assert api_client.login(inventory.manager.email, "test-password")[0] == 200
    for path in ["holds", "holds-summary"]:
        assert api_client.get(f"/api/reports/kairon/{path}?{query}")[0] == 422
