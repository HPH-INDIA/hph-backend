"""Hold counts and details share authorization and filter semantics."""

import datetime as dt
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.encryption.passwords import hash_password
from app.extensions import db
from app.kairon.models import KaironChartRecord, KaironUploadBatch
from app.roles.models import Role, RoleType
from app.users.models import Project, User


@pytest.fixture
def inventory():
    token = uuid4().hex[:10]
    roles = {code: Role.query.join(RoleType).filter(RoleType.code == code).first()
             for code in ("manager", "lead", "employee")}
    project = Project.query.filter_by(name="CODING").one()
    password = hash_password("test-password")

    def user(name, role, parent=None):
        member = User(email=f"holds-{token}-{name}@example.com", first_name="Hold", last_name=name,
                      emp_id=f"H-{token}-{name}", role_id=roles[role].id, project_id=project.id,
                      reports_to_id=parent.id if parent else None, password_hash=password,
                      first_login=False, is_active=True)
        db.session.add(member)
        db.session.flush()
        return member

    manager = user("manager", "manager")
    lead = user("lead", "lead", manager)
    coder = user("coder", "employee", lead)
    empty = user("empty", "employee", lead)
    inactive = user("inactive", "employee", lead)
    inactive.is_active = False
    inactive.last_working_day = dt.date(2026, 8, 31)
    lead2 = user("lead2", "lead", manager)
    coder2 = user("coder2", "employee", lead2)
    other_manager = user("other-manager", "manager")
    other_lead = user("other-lead", "lead", other_manager)
    outsider = user("outsider", "employee", other_lead)
    batch = KaironUploadBatch(uploaded_by_id=manager.id, status="completed")
    superseded = KaironUploadBatch(uploaded_by_id=manager.id, status="completed",
                                   superseded_at=dt.datetime.now(dt.timezone.utc))
    db.session.add_all([batch, superseded])
    db.session.flush()

    def chart(member, date="2026-09-01", practice="North", status="On Hold", upload=batch):
        record = KaironChartRecord(batch_id=upload.id, user_id=member.id, program="PVP", level="1LR",
                                  status=status, coding_analyst_raw=f"Hold {member.last_name}",
                                  created_date=dt.date.fromisoformat(date), practice=practice, actions=1)
        db.session.add(record)
        db.session.flush()
        return record

    first = chart(coder)
    last = chart(coder, "2026-09-30", "South")
    same_day = chart(coder, "2026-09-30", "South")
    chart(coder, "2026-10-01", None)
    chart(coder, "2026-08-31", "100% Care")
    chart(lead)
    chart(inactive, "2026-08-30")
    chart(lead2)
    chart(coder2, practice="South")
    chart(outsider, practice="Private outside practice")
    chart(manager)
    chart(coder, status="Completed")
    chart(coder, status="Active")
    chart(coder, upload=superseded)
    db.session.commit()
    return SimpleNamespace(manager=manager, lead=lead, coder=coder, empty=empty, inactive=inactive,
                           lead2=lead2, coder2=coder2, outsider=outsider, other_lead=other_lead,
                           first=first, last=last, same_day=same_day)


def fetch(client, path, query=""):
    status, body = client.get(f"/api/reports/kairon/{path}{query}")
    assert status == 200, body
    return body["data"]


@pytest.mark.parametrize("role,expected,coder_count,lead_count", [
    ("coder", 5, 5, 0), ("lead", 7, 6, 1), ("manager", 9, 7, 2),
])
def test_role_scope_counts_and_options(api_client, inventory, role, expected, coder_count, lead_count):
    member = getattr(inventory, role)
    assert api_client.login(member.email, "test-password")[0] == 200
    summary = fetch(api_client, "holds-summary")
    records = fetch(api_client, "holds")
    assert records["total"] == summary["total"] == expected
    assert (summary["coderCount"], summary["leadCount"]) == (coder_count, lead_count)
    assert sum(user["count"] for user in summary["users"]) == expected
    assert "Private outside practice" not in summary["practices"]
    assert inventory.outsider.id not in {user["id"] for user in summary["users"]}
    assert {item["status"] for item in records["items"]} == {"On Hold"}
    if role == "coder":
        assert {user["id"] for user in summary["users"]} == {member.id}
        assert {item["userId"] for item in records["items"]} == {member.id}
    else:
        assert next(user for user in summary["users"] if user["id"] == inventory.empty.id)["count"] == 0
        assert next(user for user in summary["users"] if user["id"] == inventory.inactive.id)["count"] == 1


@pytest.mark.parametrize("role,target", [("coder", "lead"), ("lead", "coder2"), ("manager", "outsider")])
def test_user_filters_cannot_widen_scope(api_client, inventory, role, target):
    assert api_client.login(getattr(inventory, role).email, "test-password")[0] == 200
    for endpoint in ("holds", "holds-summary"):
        assert api_client.get(f"/api/reports/kairon/{endpoint}?userId={getattr(inventory, target).id}")[0] == 404
        assert api_client.get(f"/api/reports/kairon/{endpoint}?leadId={inventory.other_lead.id}")[0] == 404


def test_lead_and_manager_views_and_team_filters(api_client, inventory):
    assert api_client.login(inventory.manager.email, "test-password")[0] == 200
    assert fetch(api_client, "holds", "?view=coders")["total"] == 7
    assert fetch(api_client, "holds", "?view=leads")["total"] == 2
    assert fetch(api_client, "holds", f"?leadId={inventory.lead.id}")["total"] == 7
    assert fetch(api_client, "holds", f"?leadId={inventory.lead.id}&view=coders")["total"] == 6
    assert fetch(api_client, "holds", f"?leadId={inventory.lead.id}&userId={inventory.coder2.id}")["total"] == 0
    assert fetch(api_client, "holds", "?leadId=unassigned")["total"] == 0
    assert api_client.login(inventory.lead.email, "test-password")[0] == 200
    assert {row["userId"] for row in fetch(api_client, "holds", "?view=leads")["items"]} == {inventory.lead.id}


def test_inclusive_creation_dates_practice_and_pagination(api_client, inventory):
    assert api_client.login(inventory.coder.email, "test-password")[0] == 200
    query = "?createdFrom=2026-09-01&createdTo=2026-09-30"
    page1 = fetch(api_client, "holds", query + "&pageSize=2")
    page2 = fetch(api_client, "holds", query + "&pageSize=2&page=2")
    assert page1["total"] == fetch(api_client, "holds-summary", query)["total"] == 3
    assert page1["totalPages"] == 2
    assert [row["id"] for row in page1["items"]] == [inventory.same_day.id, inventory.last.id]
    assert [row["id"] for row in page2["items"]] == [inventory.first.id]
    assert fetch(api_client, "holds", query + "&practice=South")["total"] == 2
    assert fetch(api_client, "holds", "?createdFrom=2026-10-01")["total"] == 1
    assert fetch(api_client, "holds", "?createdTo=2026-08-31")["total"] == 1
    assert fetch(api_client, "holds", "?practice=100%25%20Care")["total"] == 1
    assert fetch(api_client, "holds", "?practice=%25")["total"] == 0
    assert fetch(api_client, "holds", "?withoutPractice=true")["total"] == 1


def test_empty_results_keep_authorized_filter_options(api_client, inventory):
    assert api_client.login(inventory.lead.email, "test-password")[0] == 200
    query = f"?userId={inventory.empty.id}&createdFrom=2030-01-01"
    data = fetch(api_client, "holds", query)
    assert (data["items"], data["total"], data["totalPages"]) == ([], 0, 0)
    summary = fetch(api_client, "holds-summary", query)
    assert summary["total"] == 0
    assert len(summary["users"]) == 4
    assert summary["practices"] == ["100% Care", "North", "South"]


@pytest.mark.parametrize("query", [
    "createdFrom=2026-10-01&createdTo=2026-09-01", "createdFrom=invalid", "userId=0",
    "leadId=invalid", "page=0", "pageSize=101", "view=invalid", "practice=North&withoutPractice=true",
])
def test_invalid_filters(api_client, inventory, query):
    assert api_client.login(inventory.lead.email, "test-password")[0] == 200
    for endpoint in ("holds", "holds-summary"):
        assert api_client.get(f"/api/reports/kairon/{endpoint}?{query}")[0] == 422


def test_reports_permission_is_required(api_client, inventory, monkeypatch):
    assert api_client.login(inventory.lead.email, "test-password")[0] == 200
    monkeypatch.setattr("app.auth.has_feature", lambda *args, **kwargs: False)
    for endpoint in ("holds", "holds-summary"):
        assert api_client.get(f"/api/reports/kairon/{endpoint}")[0] == 403
