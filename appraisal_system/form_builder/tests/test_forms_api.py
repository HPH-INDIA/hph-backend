import pytest
from factories import (
    FormAssignmentFactory,
    FormFactory,
    FormTemplateFactory,
    published_form,
    valid_definition,
)

from models import FormEvent, FormStatus, db


def _create(client, headers, **body):
    body.setdefault("name", "Coding employee appraisal")
    return client.post("/forms", json=body, headers=headers)


class TestAuthentication:
    def test_health_check_is_public(self, client):
        assert client.get("/healthz").status_code == 200

    def test_missing_token_is_rejected(self, client):
        response = client.get("/forms", headers={"caller_id": "1"})

        assert response.status_code == 401
        assert response.json["code"] == "UNAUTHENTICATED"
        assert response.json["success"] is False

    def test_caller_id_must_match_the_token(self, client, tokens):
        headers = tokens.admin()
        headers["caller_id"] = "999"

        assert client.get("/forms", headers=headers).status_code == 401

    def test_untrusted_issuer_is_rejected(self, client, tokens):
        assert client.get("/forms", headers=tokens.stranger_service()).status_code == 401

    def test_token_for_another_service_is_rejected(self, client, tokens):
        headers = tokens.user(audience="hph-appraisal")

        assert client.get("/forms", headers=headers).status_code == 401

    def test_user_without_feature_is_forbidden(self, client, tokens):
        response = client.get("/forms", headers=tokens.nobody())

        assert response.status_code == 403
        assert response.json["code"] == "PERMISSION_DENIED"

    def test_read_only_user_cannot_write(self, client, tokens):
        assert client.get("/forms", headers=tokens.reader()).status_code == 200
        assert _create(client, tokens.reader()).status_code == 403


class TestCreateAndUpdate:
    def test_create_blank_draft(self, client, tokens):
        response = _create(client, tokens.admin(), description="For coders")

        assert response.status_code == 201
        assert response.json["status"] == "draft"
        assert response.json["draft_definition"] == {"sections": []}
        assert response.json["draft_errors"] == [
            {"path": "sections", "message": "add at least one section"}
        ]
        assert response.json["latest_version"] is None

    def test_create_from_template(self, client, tokens):
        template = FormTemplateFactory()

        response = _create(client, tokens.admin(), from_template_id=template.id)

        assert response.status_code == 201
        assert response.json["draft_definition"] == valid_definition()
        assert response.json["draft_errors"] == []

    def test_unknown_template(self, client, tokens):
        assert _create(client, tokens.admin(), from_template_id=999).status_code == 404

    def test_template_and_definition_together_are_rejected(self, client, tokens):
        template = FormTemplateFactory()

        response = _create(
            client, tokens.admin(), from_template_id=template.id, definition=valid_definition()
        )

        assert response.status_code == 400

    def test_unknown_body_property_is_rejected_by_the_spec(self, client, tokens):
        response = _create(client, tokens.admin(), colour="red")

        assert response.status_code == 400
        assert response.json["code"] == "INVALID_ARGUMENT"

    def test_update_draft_and_audit(self, client, tokens):
        form = FormFactory()

        response = client.patch(
            f"/forms/{form.id}",
            json={"name": "Renamed", "description": None},
            headers=tokens.admin(),
        )

        assert response.status_code == 200
        assert response.json["name"] == "Renamed"
        assert response.json["description"] is None
        actions = [event.action for event in db.session.query(FormEvent).all()]
        assert actions == ["updated"]

    def test_archived_form_cannot_be_edited(self, client, tokens):
        form = FormFactory(status=FormStatus.ARCHIVED)

        response = client.patch(f"/forms/{form.id}", json={"name": "X"}, headers=tokens.admin())

        assert response.status_code == 400
        assert response.json["code"] == "FAILED_PRECONDITION"

    def test_get_unknown_form(self, client, tokens):
        response = client.get("/forms/12345", headers=tokens.admin())

        assert response.status_code == 404
        assert response.json["code"] == "NOT_FOUND"


class TestPublish:
    def test_draft_with_errors_cannot_be_published(self, client, tokens):
        form = FormFactory(draft_definition={"sections": []})

        response = client.post(f"/forms/{form.id}/publish", headers=tokens.admin())

        assert response.status_code == 400
        assert response.json["data"]["errors"] == [
            {"path": "sections", "message": "add at least one section"}
        ]

    def test_publish_creates_immutable_versions(self, client, tokens):
        form = FormFactory()

        first = client.post(f"/forms/{form.id}/publish", headers=tokens.admin())
        assert first.status_code == 200
        assert first.json["status"] == "published"
        assert first.json["latest_version"]["version_no"] == 1
        assert first.json["has_unpublished_changes"] is False

        unchanged = client.post(f"/forms/{form.id}/publish", headers=tokens.admin())
        assert unchanged.status_code == 400

        edited = valid_definition()
        edited["sections"][0]["title"] = "About your cycle"
        patched = client.patch(
            f"/forms/{form.id}", json={"definition": edited}, headers=tokens.admin()
        )
        assert patched.json["has_unpublished_changes"] is True

        second = client.post(f"/forms/{form.id}/publish", headers=tokens.admin())
        assert second.json["latest_version"]["version_no"] == 2

        versions = client.get(f"/forms/{form.id}/versions", headers=tokens.admin()).json
        assert [version["version_no"] for version in versions] == [1, 2]

        version_one = client.get(f"/forms/{form.id}/versions/1", headers=tokens.admin()).json
        assert version_one["definition"]["sections"][0]["title"] == "Self-assessment"

    def test_publish_normalises_the_stored_draft(self, client, tokens):
        definition = valid_definition()
        del definition["sections"][0]["fields"][0]["max_length"]
        form = FormFactory(draft_definition=definition)

        response = client.post(f"/forms/{form.id}/publish", headers=tokens.admin())

        assert response.json["draft_definition"]["sections"][0]["fields"][0]["max_length"] == 4000


class TestDeleteAndArchive:
    def test_never_published_form_can_be_deleted(self, client, tokens):
        form = FormFactory()

        response = client.delete(f"/forms/{form.id}", headers=tokens.admin())

        assert response.status_code == 200
        assert response.json["success"] is True
        assert client.get(f"/forms/{form.id}", headers=tokens.admin()).status_code == 404

    def test_published_form_must_be_archived_instead(self, client, tokens):
        form = published_form()

        response = client.delete(f"/forms/{form.id}", headers=tokens.admin())

        assert response.status_code == 400
        assert "archive" in response.json["message"]

    def test_archive_deactivates_assignments(self, client, tokens):
        form = published_form()

        response = client.post(f"/forms/{form.id}/archive", headers=tokens.admin())

        assert response.status_code == 200
        assert response.json["status"] == "archived"
        assert response.json["assignments"] == []


class TestAssignments:
    def test_assign_project_and_role_types(self, client, tokens):
        form = FormFactory()

        response = client.put(
            f"/forms/{form.id}/assignments",
            json={"project_id": 1, "role_type_codes": ["employee", "lead"]},
            headers=tokens.admin(),
        )

        assert response.status_code == 200
        assert sorted(item["role_type_code"] for item in response.json["assignments"]) == [
            "employee",
            "lead",
        ]

    def test_replacing_assignments_keeps_one_project(self, client, tokens):
        form = FormFactory()
        url = f"/forms/{form.id}/assignments"
        client.put(
            url, json={"project_id": 1, "role_type_codes": ["employee"]}, headers=tokens.admin()
        )

        response = client.put(
            url, json={"project_id": 2, "role_type_codes": ["lead"]}, headers=tokens.admin()
        )

        assert response.json["assignments"] == [
            {"project_id": 2, "role_type_code": "lead", "active": True}
        ]

    def test_empty_list_clears_assignments(self, client, tokens):
        form = published_form()

        response = client.put(
            f"/forms/{form.id}/assignments", json={"role_type_codes": []}, headers=tokens.admin()
        )

        assert response.json["assignments"] == []

    def test_unknown_project(self, client, tokens):
        form = FormFactory()

        response = client.put(
            f"/forms/{form.id}/assignments",
            json={"project_id": 77, "role_type_codes": ["employee"]},
            headers=tokens.admin(),
        )

        assert response.status_code == 400
        assert "project 77" in response.json["message"]

    def test_manager_cannot_take_an_appraisal_form(self, client, tokens):
        form = FormFactory()

        response = client.put(
            f"/forms/{form.id}/assignments",
            json={"project_id": 1, "role_type_codes": ["manager"]},
            headers=tokens.admin(),
        )

        assert response.status_code == 400

    def test_one_active_form_per_project_and_role(self, client, tokens):
        existing = published_form(project_id=1, role_type_code="employee", name="Current form")
        form = FormFactory()

        response = client.put(
            f"/forms/{form.id}/assignments",
            json={"project_id": 1, "role_type_codes": ["employee"]},
            headers=tokens.admin(),
        )

        assert response.status_code == 400
        assert response.json["code"] == "ALREADY_EXISTS"
        assert "Current form" in response.json["message"]
        assert response.json["data"]["conflicts"] == [
            {"form_id": existing.id, "role_type_code": "employee"}
        ]

    def test_list_filters_by_assignment(self, client, tokens):
        assigned = published_form(project_id=2, role_type_code="lead")
        FormFactory()

        response = client.get("/forms?project_id=2&role_type=lead", headers=tokens.admin())

        assert [item["id"] for item in response.json] == [assigned.id]


class TestResolve:
    def test_appraisal_service_resolves_latest_published_version(self, client, tokens):
        form = published_form(project_id=1, role_type_code="employee")

        response = client.get(
            "/forms/resolve?project_id=1&role_type=employee",
            headers=tokens.appraisal_service(),
        )

        assert response.status_code == 200
        assert response.json["form_id"] == form.id
        assert response.json["version_no"] == 1
        assert response.json["definition"] == valid_definition()

    def test_draft_only_form_does_not_resolve(self, client, tokens):
        FormAssignmentFactory(form=FormFactory(), project_id=1, role_type_code="employee")

        response = client.get(
            "/forms/resolve?project_id=1&role_type=employee", headers=tokens.appraisal_service()
        )

        assert response.status_code == 404

    def test_users_need_form_builder_to_resolve(self, client, tokens):
        published_form()
        url = "/forms/resolve?project_id=1&role_type=employee"

        assert client.get(url, headers=tokens.reader()).status_code == 200
        assert client.get(url, headers=tokens.nobody()).status_code == 403

    def test_services_cannot_use_admin_endpoints(self, client, tokens):
        assert client.get("/forms", headers=tokens.appraisal_service()).status_code == 403


class TestValidateAnswersEndpoint:
    @pytest.mark.parametrize(
        "answers, mode, valid",
        [
            ({"achievements": "Done", "self_rating": 4}, "submit", True),
            ({"achievements": "Done"}, "submit", False),
            ({"achievements": "Done"}, "draft", True),
        ],
    )
    def test_validates_against_the_pinned_version(self, client, tokens, answers, mode, valid):
        form = published_form()
        version_id = form.versions[0].id

        response = client.post(
            f"/form-versions/{version_id}/validate",
            json={"answers": answers, "stage": "employee", "mode": mode},
            headers=tokens.appraisal_service(),
        )

        assert response.status_code == 200
        assert response.json["valid"] is valid

    def test_version_by_id(self, client, tokens):
        form = published_form()

        response = client.get(
            f"/form-versions/{form.versions[0].id}", headers=tokens.appraisal_service()
        )

        assert response.json["form_name"] == form.name


class TestTemplates:
    def test_lists_templates_defaults_first(self, client, tokens):
        FormTemplateFactory(name="B custom", is_default=False)
        FormTemplateFactory(name="A default", is_default=True)

        response = client.get("/form-templates", headers=tokens.reader())

        assert [item["name"] for item in response.json] == ["A default", "B custom"]
