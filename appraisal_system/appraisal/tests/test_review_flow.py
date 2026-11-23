from conftest import (
    ADMIN_ID,
    EMPLOYEE_ID,
    LEAD_ID,
    MANAGER_ID,
    ORPHAN_EMPLOYEE_ID,
    OTHER_LEAD_ID,
    OTHER_MANAGER_ID,
    SECOND_EMPLOYEE_ID,
)
from helpers import (
    EMPLOYEE_ANSWERS,
    LEAD_ANSWERS,
    MANAGER_ANSWERS,
    appraisal_id_for,
    approve_as_manager,
    complete_lead,
    launched_cycle,
    post,
    save,
    submit_filled,
)

from models import AppraisalEvent, db


def _get(client, headers, appraisal_id):
    response = client.get(f"/appraisals/{appraisal_id}", headers=headers)
    assert response.status_code == 200, response.json
    return response.json


class TestEmployeeToLeadToManager:
    def test_full_happy_path(self, client, tokens):
        launched_cycle(client, tokens)
        appraisal_id = appraisal_id_for(EMPLOYEE_ID)

        draft = save(client, tokens.employee(), appraisal_id, {"achievements": "Shipped"})
        assert draft.json["status"] == "draft"
        assert draft.json["available_actions"] == ["save_answers", "submit"]

        submitted = submit_filled(client, tokens, appraisal_id)
        assert submitted["status"] == "awaiting_review"
        assert submitted["current_stage"] == "lead"
        assert [step["status"] for step in submitted["review_chain"]] == ["active", "pending"]

        handed_off = complete_lead(client, tokens, appraisal_id)
        assert handed_off["status"] == "awaiting_review"
        assert handed_off["current_stage"] == "manager"
        assert handed_off["current_reviewer_user_id"] == MANAGER_ID

        approved = approve_as_manager(client, tokens, appraisal_id)
        assert approved["status"] == "approved"
        assert approved["available_actions"] == []
        assert [step["status"] for step in approved["review_chain"]] == ["completed", "completed"]

        actions = [
            event.action
            for event in db.session.query(AppraisalEvent).filter_by(appraisal_id=appraisal_id)
        ]
        assert actions == [
            "appraisal_created",
            "draft_started",
            "submitted",
            "lead_review_completed",
            "one_to_one_recorded",
            "approved",
        ]

    def test_approved_appraisal_is_read_only_for_everyone(self, client, tokens):
        launched_cycle(client, tokens)
        appraisal_id = appraisal_id_for(EMPLOYEE_ID)
        submit_filled(client, tokens, appraisal_id)
        complete_lead(client, tokens, appraisal_id)
        approve_as_manager(client, tokens, appraisal_id)

        assert save(client, tokens.employee(), appraisal_id, {"notes": "x"}).status_code == 400
        assert save(client, tokens.manager(), appraisal_id, MANAGER_ANSWERS).status_code == 400
        assert post(client, tokens.manager(), appraisal_id, "approve").status_code == 400

    def test_submit_requires_required_fields(self, client, tokens):
        launched_cycle(client, tokens)
        appraisal_id = appraisal_id_for(EMPLOYEE_ID)
        save(client, tokens.employee(), appraisal_id, {"achievements": "Shipped"})

        response = post(client, tokens.employee(), appraisal_id, "submit")

        assert response.status_code == 400
        assert response.json["data"]["errors"] == [
            {"path": "self_rating", "message": "is required"}
        ]

    def test_employee_cannot_answer_reviewer_fields(self, client, tokens):
        launched_cycle(client, tokens)
        appraisal_id = appraisal_id_for(EMPLOYEE_ID)

        response = save(client, tokens.employee(), appraisal_id, {"lead_comments": "great"})

        assert response.status_code == 400
        assert response.json["data"]["errors"][0]["path"] == "lead_comments"

    def test_manager_cannot_act_before_the_lead(self, client, tokens):
        launched_cycle(client, tokens)
        appraisal_id = appraisal_id_for(EMPLOYEE_ID)
        submit_filled(client, tokens, appraisal_id)

        assert save(client, tokens.manager(), appraisal_id, MANAGER_ANSWERS).status_code == 400
        assert post(client, tokens.manager(), appraisal_id, "approve").status_code == 403
        assert post(client, tokens.manager(), appraisal_id, "one-to-one", {}).status_code == 403

    def test_lead_cannot_approve(self, client, tokens):
        launched_cycle(client, tokens)
        appraisal_id = appraisal_id_for(EMPLOYEE_ID)
        submit_filled(client, tokens, appraisal_id)
        save(client, tokens.lead(), appraisal_id, LEAD_ANSWERS)

        response = post(client, tokens.lead(), appraisal_id, "approve")

        assert response.status_code == 400
        assert "belongs to the manager step" in response.json["message"]

    def test_lead_must_fill_required_fields_before_hand_off(self, client, tokens):
        launched_cycle(client, tokens)
        appraisal_id = appraisal_id_for(EMPLOYEE_ID)
        submit_filled(client, tokens, appraisal_id)

        response = post(client, tokens.lead(), appraisal_id, "reviews/complete")

        assert response.status_code == 400
        assert response.json["data"]["errors"] == [
            {"path": "lead_comments", "message": "is required"}
        ]

    def test_approval_needs_the_one_to_one(self, client, tokens):
        launched_cycle(client, tokens)
        appraisal_id = appraisal_id_for(EMPLOYEE_ID)
        submit_filled(client, tokens, appraisal_id)
        complete_lead(client, tokens, appraisal_id)
        save(client, tokens.manager(), appraisal_id, MANAGER_ANSWERS)

        before = _get(client, tokens.manager(), appraisal_id)
        response = post(client, tokens.manager(), appraisal_id, "approve")

        assert "approve" not in before["available_actions"]
        assert response.status_code == 400
        assert response.json["message"] == "record the one-to-one before approving"

    def test_cycle_can_switch_off_the_one_to_one(self, client, tokens):
        launched_cycle(client, tokens, require_one_to_one=False)
        appraisal_id = appraisal_id_for(EMPLOYEE_ID)
        submit_filled(client, tokens, appraisal_id)
        complete_lead(client, tokens, appraisal_id)
        save(client, tokens.manager(), appraisal_id, MANAGER_ANSWERS)

        assert post(client, tokens.manager(), appraisal_id, "approve").status_code == 200

    def test_one_to_one_cannot_be_in_the_future(self, client, tokens):
        launched_cycle(client, tokens)
        appraisal_id = appraisal_id_for(EMPLOYEE_ID)
        submit_filled(client, tokens, appraisal_id)
        complete_lead(client, tokens, appraisal_id)

        response = post(
            client,
            tokens.manager(),
            appraisal_id,
            "one-to-one",
            {"held_at": "2099-01-01T10:00:00Z"},
        )

        assert response.status_code == 400


class TestSendBack:
    def test_lead_sends_back_and_the_chain_restarts(self, client, tokens):
        launched_cycle(client, tokens)
        appraisal_id = appraisal_id_for(EMPLOYEE_ID)
        submit_filled(client, tokens, appraisal_id)

        returned = post(
            client, tokens.lead(), appraisal_id, "request-changes", {"feedback": "Add QA accuracy"}
        )
        assert returned.status_code == 200
        assert returned.json["status"] == "changes_requested"
        assert returned.json["review_attempt"] == 2

        employee_view = _get(client, tokens.employee(), appraisal_id)
        assert employee_view["answers"]["employee"] == EMPLOYEE_ANSWERS
        assert employee_view["comments"][0]["kind"] == "feedback"
        assert employee_view["comments"][0]["body"] == "Add QA accuracy"
        assert employee_view["previous_attempts"][0]["review_chain"][0]["status"] == "returned"

        save(
            client,
            tokens.employee(),
            appraisal_id,
            {"achievements": "Closed 3,000 charts at 98% QA"},
        )
        resubmitted = post(client, tokens.employee(), appraisal_id, "submit")
        assert resubmitted.json["current_stage"] == "lead"
        assert resubmitted.json["review_chain"][0]["attempt"] == 2

    def test_manager_can_send_back_too(self, client, tokens):
        launched_cycle(client, tokens)
        appraisal_id = appraisal_id_for(EMPLOYEE_ID)
        submit_filled(client, tokens, appraisal_id)
        complete_lead(client, tokens, appraisal_id)
        post(client, tokens.manager(), appraisal_id, "one-to-one", {})

        returned = post(
            client, tokens.manager(), appraisal_id, "request-changes", {"feedback": "Rework goals"}
        )

        assert returned.json["status"] == "changes_requested"
        assert returned.json["one_to_one_held_at"] is None
        # The whole chain runs again, including the Lead.
        resubmitted = post(client, tokens.employee(), appraisal_id, "submit")
        assert resubmitted.json["current_stage"] == "lead"

    def test_feedback_is_required(self, client, tokens):
        launched_cycle(client, tokens)
        appraisal_id = appraisal_id_for(EMPLOYEE_ID)
        submit_filled(client, tokens, appraisal_id)

        response = post(client, tokens.lead(), appraisal_id, "request-changes", {"feedback": "   "})

        assert response.status_code == 400


class TestLeadAppraisal:
    def test_lead_goes_straight_to_manager(self, client, tokens):
        launched_cycle(client, tokens)
        appraisal_id = appraisal_id_for(LEAD_ID)

        submitted = submit_filled(client, tokens, appraisal_id, headers=tokens.lead())

        assert submitted["current_stage"] == "manager"
        assert [step["stage"] for step in submitted["review_chain"]] == ["manager"]
        assert approve_as_manager(client, tokens, appraisal_id)["status"] == "approved"


class TestVisibility:
    def _submitted_and_handed_off(self, client, tokens):
        launched_cycle(client, tokens)
        appraisal_id = appraisal_id_for(EMPLOYEE_ID)
        submit_filled(client, tokens, appraisal_id)
        complete_lead(client, tokens, appraisal_id)
        save(client, tokens.manager(), appraisal_id, MANAGER_ANSWERS)
        return appraisal_id

    def test_employee_sees_reviews_only_after_approval(self, client, tokens):
        appraisal_id = self._submitted_and_handed_off(client, tokens)

        before = _get(client, tokens.employee(), appraisal_id)
        assert set(before["answers"]) == {"employee"}

        post(client, tokens.manager(), appraisal_id, "one-to-one", {"notes": "Discussed goals"})
        post(client, tokens.manager(), appraisal_id, "approve")
        after = _get(client, tokens.employee(), appraisal_id)
        assert after["answers"]["lead"] == LEAD_ANSWERS
        assert after["answers"]["manager"] == MANAGER_ANSWERS
        assert [comment["kind"] for comment in after["comments"]] == ["one_to_one_note"]

    def test_one_to_one_notes_stay_between_manager_and_employee(self, client, tokens):
        appraisal_id = self._submitted_and_handed_off(client, tokens)
        post(client, tokens.manager(), appraisal_id, "one-to-one", {"notes": "Private discussion"})
        post(client, tokens.manager(), appraisal_id, "approve")

        def kinds(headers):
            return [comment["kind"] for comment in _get(client, headers, appraisal_id)["comments"]]

        assert kinds(tokens.manager()) == ["one_to_one_note"]
        assert kinds(tokens.employee()) == ["one_to_one_note"]
        assert kinds(tokens.admin()) == ["one_to_one_note"]
        # Even after approval, when the Lead sees every stage's answers.
        lead_view = _get(client, tokens.lead(), appraisal_id)
        assert lead_view["comments"] == []
        assert lead_view["answers"]["manager"] == MANAGER_ANSWERS

    def test_lead_does_not_see_the_managers_answers_before_approval(self, client, tokens):
        appraisal_id = self._submitted_and_handed_off(client, tokens)

        lead_view = _get(client, tokens.lead(), appraisal_id)

        assert set(lead_view["answers"]) == {"employee", "lead"}

    def test_manager_and_admin_see_everything(self, client, tokens):
        appraisal_id = self._submitted_and_handed_off(client, tokens)

        for headers in (tokens.manager(), tokens.admin()):
            assert set(_get(client, headers, appraisal_id)["answers"]) == {
                "employee",
                "lead",
                "manager",
            }

    def test_unrelated_people_are_refused(self, client, tokens):
        launched_cycle(client, tokens)
        appraisal_id = appraisal_id_for(EMPLOYEE_ID)

        for headers in (
            tokens.employee(SECOND_EMPLOYEE_ID),
            tokens.lead(OTHER_LEAD_ID),
            tokens.manager(OTHER_MANAGER_ID),
        ):
            response = client.get(f"/appraisals/{appraisal_id}", headers=headers)
            assert response.status_code == 403

    def test_my_appraisals(self, client, tokens):
        launched_cycle(client, tokens)

        response = client.get("/me/appraisals", headers=tokens.employee())

        assert [item["user_id"] for item in response.json] == [EMPLOYEE_ID]


class TestInbox:
    def test_active_and_completed(self, client, tokens):
        launched_cycle(client, tokens)
        first = appraisal_id_for(EMPLOYEE_ID)
        second = appraisal_id_for(SECOND_EMPLOYEE_ID)
        submit_filled(client, tokens, first)
        submit_filled(client, tokens, second, headers=tokens.employee(SECOND_EMPLOYEE_ID))
        complete_lead(client, tokens, first)

        lead_active = client.get("/reviews/inbox", headers=tokens.lead()).json
        lead_done = client.get("/reviews/inbox?status=completed", headers=tokens.lead()).json
        manager_active = client.get("/reviews/inbox", headers=tokens.manager()).json

        assert [item["appraisal_id"] for item in lead_active] == [second]
        assert [item["appraisal_id"] for item in lead_done] == [first]
        assert [(item["appraisal_id"], item["stage"]) for item in manager_active] == [
            (first, "manager")
        ]

    def test_employees_have_no_inbox(self, client, tokens):
        assert client.get("/reviews/inbox", headers=tokens.employee()).status_code == 403


class TestReassign:
    def test_admin_completes_a_missing_chain(self, client, tokens):
        launched_cycle(client, tokens)
        appraisal_id = appraisal_id_for(ORPHAN_EMPLOYEE_ID)
        url = f"/appraisals/{appraisal_id}/reviewer"

        lead_set = client.patch(
            url, json={"stage": "lead", "reviewer_user_id": LEAD_ID}, headers=tokens.admin()
        )
        assert lead_set.json["status"] == "needs_reviewer"
        manager_set = client.patch(
            url, json={"stage": "manager", "reviewer_user_id": MANAGER_ID}, headers=tokens.admin()
        )

        assert manager_set.status_code == 200
        assert manager_set.json["status"] == "not_started"
        assert manager_set.json["needs_reviewer_reason"] is None

    def test_reviewer_must_have_the_matching_role(self, client, tokens):
        launched_cycle(client, tokens)
        appraisal_id = appraisal_id_for(ORPHAN_EMPLOYEE_ID)

        response = client.patch(
            f"/appraisals/{appraisal_id}/reviewer",
            json={"stage": "lead", "reviewer_user_id": MANAGER_ID},
            headers=tokens.admin(),
        )

        assert response.status_code == 400
        assert "lead role type" in response.json["message"]

    def test_active_step_moves_to_the_new_reviewer(self, client, tokens):
        launched_cycle(client, tokens)
        appraisal_id = appraisal_id_for(EMPLOYEE_ID)
        submit_filled(client, tokens, appraisal_id)

        response = client.patch(
            f"/appraisals/{appraisal_id}/reviewer",
            json={"stage": "lead", "reviewer_user_id": OTHER_LEAD_ID},
            headers=tokens.admin(),
        )

        assert response.json["current_reviewer_user_id"] == OTHER_LEAD_ID
        assert (
            client.get("/reviews/inbox", headers=tokens.lead(OTHER_LEAD_ID)).json[0]["appraisal_id"]
            == appraisal_id
        )

    def test_completed_step_cannot_be_reassigned(self, client, tokens):
        launched_cycle(client, tokens)
        appraisal_id = appraisal_id_for(EMPLOYEE_ID)
        submit_filled(client, tokens, appraisal_id)
        complete_lead(client, tokens, appraisal_id)

        response = client.patch(
            f"/appraisals/{appraisal_id}/reviewer",
            json={"stage": "lead", "reviewer_user_id": OTHER_LEAD_ID},
            headers=tokens.admin(),
        )

        assert response.status_code == 400
        assert "already complete" in response.json["message"]

    def test_only_cycle_admins_reassign(self, client, tokens):
        launched_cycle(client, tokens)
        appraisal_id = appraisal_id_for(EMPLOYEE_ID)

        response = client.patch(
            f"/appraisals/{appraisal_id}/reviewer",
            json={"stage": "lead", "reviewer_user_id": OTHER_LEAD_ID},
            headers=tokens.manager(),
        )

        assert response.status_code == 403


class TestClosedCycle:
    def test_nothing_changes_after_close(self, client, tokens):
        cycle, _ = launched_cycle(client, tokens)
        appraisal_id = appraisal_id_for(EMPLOYEE_ID)
        client.post(f"/cycles/{cycle['id']}/close", headers=tokens.admin())

        response = save(client, tokens.employee(), appraisal_id, {"notes": "late"})

        assert response.status_code == 400
        assert _get(client, tokens.employee(), appraisal_id)["available_actions"] == []


class TestProgress:
    def test_counts_by_status_and_stage(self, client, tokens):
        cycle, _ = launched_cycle(client, tokens)
        submit_filled(client, tokens, appraisal_id_for(EMPLOYEE_ID))
        lead_appraisal = appraisal_id_for(LEAD_ID)
        submit_filled(client, tokens, lead_appraisal, headers=tokens.lead())

        progress = client.get(f"/cycles/{cycle['id']}/progress", headers=tokens.admin()).json

        assert progress["total"] == 6
        assert progress["by_status"]["awaiting_review"] == 2
        assert progress["by_status"]["needs_reviewer"] == 2
        assert progress["awaiting_review_by_stage"] == {"lead": 1, "manager": 1}
        assert {item["user_id"] for item in progress["needs_reviewer"]} == {ORPHAN_EMPLOYEE_ID, 21}
        assert progress["skipped_at_launch"]

    def test_reviewers_only_count_their_own(self, client, tokens):
        cycle, _ = launched_cycle(client, tokens)

        lead_view = client.get(f"/cycles/{cycle['id']}/progress", headers=tokens.lead()).json

        assert lead_view["total"] == 2
        assert "skipped_at_launch" not in lead_view

    def test_admin_id_is_not_appraised(self, client, tokens):
        launched_cycle(client, tokens)

        assert appraisal_id_for(ADMIN_ID) is None
