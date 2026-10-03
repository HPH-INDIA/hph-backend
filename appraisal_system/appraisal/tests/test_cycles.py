import pytest
from conftest import CODING_PROJECT
from factories import CycleFactory
from helpers import CYCLE_BODY, create_cycle, launch

from models import AppraisalEvent, CycleStatus, db


class TestAuthentication:
    def test_health_check_is_public(self, client):
        assert client.get("/healthz").status_code == 200

    def test_missing_token(self, client):
        response = client.get("/cycles", headers={"caller_id": "1"})

        assert response.status_code == 401
        assert response.json == {
            "data": None,
            "message": "missing bearer token",
            "success": False,
            "code": "UNAUTHENTICATED",
        }

    def test_caller_id_must_match_token(self, client, tokens):
        headers = tokens.admin()
        headers["caller_id"] = "30"

        assert client.get("/cycles", headers=headers).status_code == 401

    def test_service_tokens_cannot_use_user_endpoints(self, client, tokens):
        response = client.get("/cycles", headers=tokens.service())

        assert response.status_code == 403


class TestCreateCycle:
    def test_admin_creates_a_draft(self, client, tokens):
        cycle = create_cycle(client, tokens.admin())

        assert cycle["status"] == "draft"
        assert cycle["frequency"] == "annual"
        assert cycle["require_one_to_one"] is True
        assert cycle["project_id"] is None
        assert [event.action for event in db.session.query(AppraisalEvent)] == ["cycle_created"]

    def test_project_cycle(self, client, tokens):
        cycle = create_cycle(
            client,
            tokens.admin(),
            scope="project",
            project_id=CODING_PROJECT,
            frequency="quarterly",
        )

        assert cycle["scope"] == "project"
        assert cycle["project_id"] == CODING_PROJECT

    @pytest.mark.parametrize(
        "overrides, message",
        [
            ({"scope": "project"}, "project_id is required"),
            ({"project_id": CODING_PROJECT}, "must not have a project_id"),
            ({"scope": "project", "project_id": 99}, "project 99 does not exist"),
            ({"end_date": "2025-12-31"}, "end_date must not be before start_date"),
            ({"submission_due": "2025-06-01"}, "submission_due must not be before start_date"),
            ({"review_due": "2026-12-01"}, "review_due must not be before submission_due"),
            ({"start_date": "not-a-date"}, "start_date must be a date"),
        ],
    )
    def test_rejects_inconsistent_cycles(self, client, tokens, overrides, message):
        response = client.post("/cycles", json={**CYCLE_BODY, **overrides}, headers=tokens.admin())

        assert response.status_code == 400
        assert message in response.json["message"]

    def test_due_dates_may_fall_after_the_period(self, client, tokens):
        # Annual appraisals are usually filled in after the year they cover.
        cycle = create_cycle(
            client, tokens.admin(), submission_due="2027-01-15", review_due="2027-01-31"
        )

        assert cycle["submission_due"] == "2027-01-15"

    def test_unknown_property_rejected_by_spec(self, client, tokens):
        response = client.post("/cycles", json={**CYCLE_BODY, "owner": 5}, headers=tokens.admin())

        assert response.status_code == 400
        assert response.json["code"] == "INVALID_ARGUMENT"

    def test_only_cycle_admins_create(self, client, tokens):
        assert client.post("/cycles", json=CYCLE_BODY, headers=tokens.manager()).status_code == 403


class TestVisibility:
    def test_reviewers_see_only_launched_cycles(self, client, tokens):
        CycleFactory(name="Draft", status=CycleStatus.DRAFT)
        CycleFactory(name="Running", status=CycleStatus.ACTIVE)

        admin_names = [
            cycle["name"] for cycle in client.get("/cycles", headers=tokens.admin()).json
        ]
        manager_names = [
            cycle["name"] for cycle in client.get("/cycles", headers=tokens.manager()).json
        ]

        assert sorted(admin_names) == ["Draft", "Running"]
        assert manager_names == ["Running"]

    def test_employees_cannot_list_cycles(self, client, tokens):
        assert client.get("/cycles", headers=tokens.employee()).status_code == 403

    def test_launch_summary_is_admin_only(self, client, tokens):
        cycle = CycleFactory(launch_summary={"created": 1, "skipped": []})

        admin_view = client.get(f"/cycles/{cycle.id}", headers=tokens.admin()).json
        manager_view = client.get(f"/cycles/{cycle.id}", headers=tokens.manager()).json

        assert admin_view["launch_summary"] == {"created": 1, "skipped": []}
        assert "launch_summary" not in manager_view


class TestUpdateAndClose:
    def test_draft_can_change_anything(self, client, tokens):
        cycle = create_cycle(client, tokens.admin())

        response = client.patch(
            f"/cycles/{cycle['id']}",
            json={"scope": "project", "project_id": CODING_PROJECT, "require_one_to_one": False},
            headers=tokens.admin(),
        )

        assert response.status_code == 200
        assert response.json["require_one_to_one"] is False

    def test_active_cycle_only_changes_name_and_due_dates(self, client, tokens):
        cycle = create_cycle(client, tokens.admin())
        launch(client, tokens.admin(), cycle["id"])

        ok = client.patch(
            f"/cycles/{cycle['id']}", json={"review_due": "2027-01-15"}, headers=tokens.admin()
        )
        locked = client.patch(
            f"/cycles/{cycle['id']}", json={"start_date": "2026-02-01"}, headers=tokens.admin()
        )

        assert ok.status_code == 200
        assert locked.status_code == 400
        assert "start_date cannot change once the cycle is active" in locked.json["message"]

    def test_close_locks_the_cycle(self, client, tokens):
        cycle = create_cycle(client, tokens.admin())
        launch(client, tokens.admin(), cycle["id"])

        closed = client.post(f"/cycles/{cycle['id']}/close", headers=tokens.admin())
        edit = client.patch(f"/cycles/{cycle['id']}", json={"name": "X"}, headers=tokens.admin())
        relaunch = client.post(f"/cycles/{cycle['id']}/launch", headers=tokens.admin())

        assert closed.json["status"] == "closed"
        assert edit.status_code == 400
        assert relaunch.status_code == 400

    def test_draft_cannot_be_closed(self, client, tokens):
        cycle = create_cycle(client, tokens.admin())

        response = client.post(f"/cycles/{cycle['id']}/close", headers=tokens.admin())

        assert response.status_code == 400
        assert response.json["code"] == "FAILED_PRECONDITION"
