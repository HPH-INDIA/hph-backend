import pytest
from conftest import (
    CODING_PROJECT,
    DEPARTED_EMPLOYEE_ID,
    EMPLOYEE_FORM_VERSION,
    EMPLOYEE_ID,
    INACTIVE_EMPLOYEE_ID,
    LEAD_FORM_VERSION,
    LEAD_ID,
    MANAGER_ID,
    ORPHAN_EMPLOYEE_ID,
    ORPHAN_LEAD_ID,
    OTHER_LEAD_ID,
    RCM_EMPLOYEE_ID,
    SECOND_EMPLOYEE_ID,
    directory_user,
)
from helpers import create_cycle, launch, launched_cycle

from models import Appraisal, AppraisalStatus, NotificationOutbox, db
from services.launch import resolve_review_chain


def _appraisals():
    return {appraisal.user_id: appraisal for appraisal in db.session.query(Appraisal).all()}


class TestResolveReviewChain:
    def _users(self, *users):
        return {user.id: user for user in users}

    def test_employee_goes_through_lead_then_manager(self):
        manager = directory_user(10, "manager")
        lead = directory_user(20, "lead", reports_to_id=10)
        employee = directory_user(30, "employee", reports_to_id=20)

        chain = resolve_review_chain(employee, self._users(manager, lead, employee))

        assert (chain.lead.id, chain.manager.id, chain.problem) == (20, 10, None)

    def test_lead_goes_straight_to_manager(self):
        manager = directory_user(10, "manager")
        lead = directory_user(20, "lead", reports_to_id=10)

        chain = resolve_review_chain(lead, self._users(manager, lead))

        assert (chain.lead, chain.manager.id, chain.problem) == (None, 10, None)

    @pytest.mark.parametrize(
        "users, problem",
        [
            # Employee -> Manager directly is out of scope for v1.
            (
                [directory_user(10, "manager"), directory_user(30, "employee", reports_to_id=10)],
                "no active lead in the employee's reporting line",
            ),
            (
                [
                    directory_user(20, "lead", reports_to_id=10, is_active=False),
                    directory_user(10, "manager"),
                    directory_user(30, "employee", reports_to_id=20),
                ],
                "no active lead in the employee's reporting line",
            ),
            (
                [directory_user(20, "lead"), directory_user(30, "employee", reports_to_id=20)],
                "the employee's lead has no active manager",
            ),
        ],
    )
    def test_incomplete_lines_are_reported(self, users, problem):
        employee = next(user for user in users if user.id == 30)

        chain = resolve_review_chain(employee, self._users(*users))

        assert chain.problem == problem


class TestLaunch:
    def test_creates_appraisals_for_employees_and_leads_with_forms(self, client, tokens):
        cycle, result = launched_cycle(client, tokens)
        appraisals = _appraisals()

        assert result["cycle"]["status"] == "active"
        # Coding: 3 employees (one orphan) + 3 leads (one orphan, one under another manager).
        assert sorted(appraisals) == sorted(
            [
                EMPLOYEE_ID,
                SECOND_EMPLOYEE_ID,
                ORPHAN_EMPLOYEE_ID,
                LEAD_ID,
                ORPHAN_LEAD_ID,
                OTHER_LEAD_ID,
            ]
        )
        assert result["created"] == 6
        assert result["needs_reviewer"] == 2
        assert INACTIVE_EMPLOYEE_ID not in appraisals
        assert DEPARTED_EMPLOYEE_ID not in appraisals

    def test_snapshots_form_and_review_chain(self, client, tokens):
        launched_cycle(client, tokens)
        employee = _appraisals()[EMPLOYEE_ID]
        lead = _appraisals()[LEAD_ID]

        assert employee.status == AppraisalStatus.NOT_STARTED
        assert employee.form_version_id == EMPLOYEE_FORM_VERSION
        assert employee.form_definition["sections"][1]["review_stage"] == "lead"
        assert (employee.lead_user_id, employee.manager_user_id) == (LEAD_ID, MANAGER_ID)
        assert employee.lead_name == "Lead 20"
        assert lead.form_version_id == LEAD_FORM_VERSION
        assert (lead.lead_user_id, lead.manager_user_id) == (None, MANAGER_ID)

    def test_incomplete_reporting_lines_need_a_reviewer(self, client, tokens):
        launched_cycle(client, tokens)
        appraisals = _appraisals()

        assert appraisals[ORPHAN_EMPLOYEE_ID].status == AppraisalStatus.NEEDS_REVIEWER
        assert appraisals[ORPHAN_EMPLOYEE_ID].needs_reviewer_reason == (
            "no active lead in the employee's reporting line"
        )
        assert appraisals[ORPHAN_LEAD_ID].status == AppraisalStatus.NEEDS_REVIEWER

    def test_people_without_a_form_are_skipped_and_listed(self, client, tokens):
        _, result = launched_cycle(client, tokens)

        skipped = {item["user_id"]: item["reason"] for item in result["skipped"]}
        assert skipped[RCM_EMPLOYEE_ID] == "no published form for employee in project 2"

    def test_project_cycle_only_covers_its_project(self, client, tokens):
        cycle = create_cycle(client, tokens.admin(), scope="project", project_id=CODING_PROJECT)
        result = launch(client, tokens.admin(), cycle["id"])

        assert result["skipped"] == []
        assert RCM_EMPLOYEE_ID not in _appraisals()

    def test_relaunch_only_adds_newcomers(self, client, tokens, directory):
        cycle, _ = launched_cycle(client, tokens)
        directory.add(directory_user(35, "employee", reports_to_id=LEAD_ID))

        result = launch(client, tokens.admin(), cycle["id"])

        assert result["created"] == 1
        assert 35 in _appraisals()
        assert len(_appraisals()) == 7

    def test_queues_an_opening_email_per_appraisal(self, client, tokens):
        launched_cycle(client, tokens)

        recipients = {
            row.recipient_user_id
            for row in db.session.query(NotificationOutbox).filter_by(event_type="appraisal_opened")
        }
        assert recipients == set(_appraisals())

    def test_directory_outage_saves_nothing(self, client, tokens, directory):
        cycle = create_cycle(client, tokens.admin())
        directory.fail = True

        response = client.post(f"/cycles/{cycle['id']}/launch", headers=tokens.admin())

        assert response.status_code == 503
        assert response.json["code"] == "UNAVAILABLE"
        assert _appraisals() == {}
        assert (
            client.get(f"/cycles/{cycle['id']}", headers=tokens.admin()).json["status"] == "draft"
        )

    def test_only_cycle_admins_launch(self, client, tokens):
        cycle = create_cycle(client, tokens.admin())

        response = client.post(f"/cycles/{cycle['id']}/launch", headers=tokens.manager())

        assert response.status_code == 403
