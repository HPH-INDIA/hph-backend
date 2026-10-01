"""The lead's Kairon report includes only direct coders and completed charts."""

import datetime as dt
from uuid import uuid4

from app.encryption.passwords import hash_password
from app.extensions import db
from app.kairon.models import KaironChartRecord, KaironUploadBatch
from app.roles.models import Role, RoleType
from app.users.models import Project, User


def test_kairon_lead_range_separates_own_charts_and_direct_coders(api_client):
    token = uuid4().hex[:10]
    roles = {
        code: Role.query.join(RoleType).filter(RoleType.code == code).first()
        for code in ("manager", "lead", "employee")
    }
    project = Project.query.filter_by(name="CODING").one()

    def member(name, role, reports_to=None):
        user = User(
            email=f"kairon-report-{token}-{name}@example.com",
            first_name=f"Kairon {token}",
            last_name=name,
            emp_id=f"KR-{token}-{name}",
            role_id=roles[role].id,
            project_id=project.id,
            reports_to_id=reports_to.id if reports_to else None,
            password_hash=hash_password("test-password"),
            first_login=False,
            is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        return user

    manager = member("manager", "manager")
    lead = member("lead", "lead", manager)
    coder = member("coder", "employee", lead)
    no_charts = member("no-charts", "employee", lead)
    other_lead = member("other-lead", "lead", manager)
    outsider = member("outsider", "employee", other_lead)
    batch = KaironUploadBatch(as_of_date=dt.date(2026, 9, 30), uploaded_by_id=manager.id, status="completed")
    superseded = KaironUploadBatch(
        as_of_date=dt.date(2026, 9, 29), uploaded_by_id=manager.id,
        status="completed", superseded_at=dt.datetime.now(dt.timezone.utc),
    )
    db.session.add_all([batch, superseded])
    db.session.flush()

    def chart(user, day, status="Completed", upload=batch):
        db.session.add(KaironChartRecord(
            batch_id=upload.id, program="PVP", level="1LR", status=status,
            user_id=user.id, coding_analyst_raw=f"{user.first_name} {user.last_name}",
            actions=1, created_date=day, completed_date=day if status == "Completed" else None,
        ))

    day_one, day_two = dt.date(2026, 9, 10), dt.date(2026, 9, 11)
    chart(lead, day_one)
    chart(lead, day_two)
    chart(coder, day_one)
    chart(coder, day_one)
    chart(coder, day_two)
    chart(coder, day_one, status="Active")
    chart(coder, day_one, upload=superseded)
    chart(outsider, day_one)
    chart(coder, dt.date(2026, 9, 12))
    db.session.commit()

    assert api_client.login(lead.email, "test-password")[0] == 200
    status, body = api_client.get("/api/reports/kairon/team-range?fromDate=2026-09-10&toDate=2026-09-11")
    assert status == 200, body
    report = body["data"]
    assert (report["fromDate"], report["toDate"]) == ("2026-09-10", "2026-09-11")
    assert report["lead"]["id"] == lead.id
    assert report["leadDays"] == [
        {"date": "2026-09-11", "count": 1},
        {"date": "2026-09-10", "count": 1},
    ]
    assert {item["user"]["id"]: (item["count"], item["days"]) for item in report["coders"]} == {
        coder.id: (3, [{"date": "2026-09-11", "count": 1}, {"date": "2026-09-10", "count": 2}]),
        no_charts.id: (0, []),
    }

    status, body = api_client.get("/api/reports/kairon/team-range?fromDate=2026-09-11&toDate=2026-09-11")
    assert status == 200, body
    assert body["data"]["leadDays"] == [{"date": "2026-09-11", "count": 1}]
    assert {item["user"]["id"]: item["count"] for item in body["data"]["coders"]} == {
        coder.id: 1, no_charts.id: 0,
    }

    status, _ = api_client.get("/api/reports/kairon/team-range?fromDate=2026-09-12&toDate=2026-09-11")
    assert status == 422

    assert api_client.login(coder.email, "test-password")[0] == 200
    assert api_client.get("/api/reports/kairon/team-range?fromDate=2026-09-10&toDate=2026-09-11")[0] == 403
    assert api_client.login(manager.email, "test-password")[0] == 200
    assert api_client.get("/api/reports/kairon/team-range?fromDate=2026-09-10&toDate=2026-09-11")[0] == 403
