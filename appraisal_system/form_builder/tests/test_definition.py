import importlib.util
from pathlib import Path

import pytest
from factories import valid_definition

from services.definition import validate_answers, validate_definition


def _paths(errors):
    return {error["path"] for error in errors}


def _load_template_migration():
    path = next(
        (Path(__file__).resolve().parents[1] / "migrations" / "versions").glob(
            "*_seed_default_appraisal_templates.py"
        )
    )
    spec = importlib.util.spec_from_file_location("seed_templates", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestValidateDefinition:
    def test_valid_definition_is_unchanged(self):
        normalised, errors = validate_definition(valid_definition())

        assert errors == []
        assert normalised == valid_definition()

    def test_fills_defaults(self):
        definition = {
            "sections": [
                {
                    "key": "about",
                    "title": "  About you  ",
                    "filled_by": "employee",
                    "fields": [{"key": "summary", "type": "short_text", "label": "Summary"}],
                }
            ]
        }

        normalised, errors = validate_definition(definition)

        assert errors == []
        field = normalised["sections"][0]["fields"][0]
        assert field["required"] is False
        assert field["max_length"] == 200
        assert normalised["sections"][0]["title"] == "About you"

    def test_not_an_object(self):
        normalised, errors = validate_definition(["sections"])

        assert normalised is None
        assert _paths(errors) == {"definition"}

    def test_empty_draft_lists_what_is_missing(self):
        _, errors = validate_definition({"sections": []})

        assert errors == [{"path": "sections", "message": "add at least one section"}]

    def test_collects_every_problem_at_once(self):
        definition = {
            "sections": [
                {
                    "key": "Bad Key",
                    "title": "",
                    "filled_by": "employee",
                    "review_stage": "lead",
                    "colour": "red",
                    "fields": [
                        {"key": "dup", "type": "short_text", "label": "One", "max_length": 9999},
                        {"key": "dup", "type": "short_text", "label": "Two"},
                        {
                            "key": "scale",
                            "type": "rating",
                            "label": "Rate",
                            "scale": {"min": 5, "max": 1},
                        },
                        {
                            "key": "pick",
                            "type": "single_choice",
                            "label": "Pick",
                            "options": [
                                {"value": "a", "label": "A"},
                                {"value": "a", "label": "Again"},
                            ],
                        },
                        {
                            "key": "many",
                            "type": "multi_choice",
                            "label": "Many",
                            "min_selected": 3,
                            "max_selected": 2,
                            "options": [{"value": "a", "label": "A"}],
                        },
                        {
                            "key": "when",
                            "type": "date",
                            "label": "When",
                            "min": "2026-12-01",
                            "max": "2026-01-01",
                        },
                        {"key": "odd", "type": "colour_picker", "label": "Odd"},
                    ],
                },
                {
                    "key": "review",
                    "title": "Review",
                    "filled_by": "reviewer",
                    "fields": [{"key": "ok", "type": "yes_no", "label": "OK?"}],
                },
            ]
        }

        _, errors = validate_definition(definition)
        paths = _paths(errors)

        assert "sections[0].key" in paths
        assert "sections[0].title" in paths
        assert "sections[0].review_stage" in paths
        assert "sections[0].colour" in paths
        assert "sections[0].fields[0].max_length" in paths
        assert "sections[0].fields[1].key" in paths
        assert "sections[0].fields[2].scale" in paths
        assert "sections[0].fields[3].options[1].value" in paths
        assert "sections[0].fields[4].min_selected" in paths
        assert "sections[0].fields[4].max_selected" in paths
        assert "sections[0].fields[5].min" in paths
        assert "sections[0].fields[6].type" in paths
        assert "sections[1].review_stage" in paths

    def test_field_keys_must_be_unique_across_sections(self):
        definition = valid_definition()
        definition["sections"][1]["fields"][0]["key"] = "achievements"

        _, errors = validate_definition(definition)

        assert "sections[1].fields[0].key" in _paths(errors)

    def test_requires_an_employee_section(self):
        definition = valid_definition()
        definition["sections"] = definition["sections"][1:]

        _, errors = validate_definition(definition)

        assert {
            "path": "sections",
            "message": "add at least one section the employee fills",
        } in errors

    @pytest.mark.parametrize("template_index", [0, 1])
    def test_seeded_templates_are_publishable(self, template_index):
        templates = _load_template_migration().TEMPLATES

        normalised, errors = validate_definition(templates[template_index]["definition"])

        assert errors == []
        assert normalised["sections"]


class TestValidateAnswers:
    def test_employee_draft_may_be_partial(self):
        normalised, errors = validate_answers(
            valid_definition(), {"achievements": "  Shipped it  "}, "employee", "draft"
        )

        assert errors == []
        assert normalised == {"achievements": "Shipped it"}

    def test_submit_requires_required_fields_of_the_stage_only(self):
        _, errors = validate_answers(
            valid_definition(), {"achievements": "Done"}, "employee", "submit"
        )

        assert errors == [{"path": "self_rating", "message": "is required"}]

    def test_blank_text_counts_as_missing_on_submit(self):
        normalised, errors = validate_answers(
            valid_definition(), {"achievements": "   ", "self_rating": 4}, "employee", "submit"
        )

        assert normalised["achievements"] is None
        assert errors == [{"path": "achievements", "message": "is required"}]

    def test_stage_cannot_answer_another_stages_fields(self):
        _, errors = validate_answers(
            valid_definition(), {"lead_comments": "Nice", "nope": 1}, "employee", "draft"
        )

        assert errors == [
            {"path": "lead_comments", "message": "is filled at the lead stage, not employee"},
            {"path": "nope", "message": "is not a field of this form"},
        ]

    def test_rating_must_be_on_scale(self):
        _, errors = validate_answers(valid_definition(), {"manager_rating": 9}, "manager", "draft")

        assert errors == [
            {"path": "manager_rating", "message": "must be a whole number from 1 to 5"}
        ]

    def test_type_checks(self):
        definition = {
            "sections": [
                {
                    "key": "s",
                    "title": "S",
                    "filled_by": "employee",
                    "fields": [
                        {
                            "key": "count",
                            "type": "number",
                            "label": "Count",
                            "min": 0,
                            "max": 10,
                            "integer": True,
                        },
                        {"key": "agree", "type": "yes_no", "label": "Agree"},
                        {"key": "when", "type": "date", "label": "When", "min": "2026-01-01"},
                        {
                            "key": "tags",
                            "type": "multi_choice",
                            "label": "Tags",
                            "min_selected": 2,
                            "options": [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}],
                        },
                        {
                            "key": "one",
                            "type": "single_choice",
                            "label": "One",
                            "options": [{"value": "x", "label": "X"}],
                        },
                    ],
                }
            ]
        }
        definition, problems = validate_definition(definition)
        assert problems == []

        _, errors = validate_answers(
            definition,
            {"count": 2.5, "agree": "yes", "when": "2025-12-31", "tags": ["a", "a"], "one": "y"},
            "employee",
            "draft",
        )

        assert _paths(errors) == {"count", "agree", "when", "tags", "one"}

    def test_multi_choice_minimum_only_applies_on_submit(self):
        definition, _ = validate_definition(
            {
                "sections": [
                    {
                        "key": "s",
                        "title": "S",
                        "filled_by": "employee",
                        "fields": [
                            {
                                "key": "tags",
                                "type": "multi_choice",
                                "label": "Tags",
                                "min_selected": 2,
                                "options": [
                                    {"value": "a", "label": "A"},
                                    {"value": "b", "label": "B"},
                                ],
                            }
                        ],
                    }
                ]
            }
        )

        assert validate_answers(definition, {"tags": ["a"]}, "employee", "draft")[1] == []
        assert validate_answers(definition, {"tags": ["a"]}, "employee", "submit")[1] == [
            {"path": "tags", "message": "must select at least 2 options"}
        ]

    def test_rejects_unknown_stage_and_mode(self):
        assert validate_answers(valid_definition(), {}, "peer", "draft")[1][0]["path"] == "stage"
        assert validate_answers(valid_definition(), {}, "employee", "final")[1][0]["path"] == "mode"
