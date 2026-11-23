"""Request helpers shared by the appraisal API tests."""

from models import Appraisal, db


CYCLE_BODY = {
    "name": "FY2026 annual",
    "scope": "organization",
    "frequency": "annual",
    "start_date": "2026-01-01",
    "end_date": "2026-12-31",
    "submission_due": "2026-12-15",
    "review_due": "2026-12-31",
}

EMPLOYEE_ANSWERS = {"achievements": "Closed 3,000 charts", "self_rating": 4}
LEAD_ANSWERS = {"lead_comments": "Consistent and accurate"}
MANAGER_ANSWERS = {"manager_rating": 4}


def create_cycle(client, headers, **overrides):
    response = client.post("/cycles", json={**CYCLE_BODY, **overrides}, headers=headers)
    assert response.status_code == 201, response.json
    return response.json


def launch(client, headers, cycle_id):
    response = client.post(f"/cycles/{cycle_id}/launch", headers=headers)
    assert response.status_code == 200, response.json
    return response.json


def launched_cycle(client, tokens, **overrides):
    cycle = create_cycle(client, tokens.admin(), **overrides)
    result = launch(client, tokens.admin(), cycle["id"])
    return cycle, result


def appraisal_id_for(user_id):
    return db.session.query(Appraisal.id).filter(Appraisal.user_id == user_id).scalar()


def save(client, headers, appraisal_id, answers):
    return client.put(
        f"/appraisals/{appraisal_id}/answers", json={"answers": answers}, headers=headers
    )


def post(client, headers, appraisal_id, action, body=None):
    return client.post(f"/appraisals/{appraisal_id}/{action}", json=body, headers=headers)


def submit_filled(client, tokens, appraisal_id, headers=None):
    headers = headers or tokens.employee()
    assert save(client, headers, appraisal_id, EMPLOYEE_ANSWERS).status_code == 200
    response = post(client, headers, appraisal_id, "submit")
    assert response.status_code == 200, response.json
    return response.json


def complete_lead(client, tokens, appraisal_id):
    assert save(client, tokens.lead(), appraisal_id, LEAD_ANSWERS).status_code == 200
    response = post(client, tokens.lead(), appraisal_id, "reviews/complete")
    assert response.status_code == 200, response.json
    return response.json


def approve_as_manager(client, tokens, appraisal_id):
    assert save(client, tokens.manager(), appraisal_id, MANAGER_ANSWERS).status_code == 200
    assert post(client, tokens.manager(), appraisal_id, "one-to-one", {}).status_code == 200
    response = post(client, tokens.manager(), appraisal_id, "approve")
    assert response.status_code == 200, response.json
    return response.json
