"""Validation of form definitions (FR-5, FR-6) and of answers given against them.

Errors are collected rather than raised one at a time, so the builder UI can show every problem
with a draft at once. Each error is ``{"path": ..., "message": ...}``.
"""

import re
from datetime import date


FIELD_TYPES = (
    "short_text",
    "long_text",
    "number",
    "rating",
    "single_choice",
    "multi_choice",
    "yes_no",
    "date",
)

FILLED_BY_EMPLOYEE = "employee"
FILLED_BY_REVIEWER = "reviewer"
FILLED_BY = (FILLED_BY_EMPLOYEE, FILLED_BY_REVIEWER)

REVIEW_STAGES = ("lead", "manager")
ANSWER_STAGES = ("employee",) + REVIEW_STAGES

MODE_DRAFT = "draft"
MODE_SUBMIT = "submit"
MODES = (MODE_DRAFT, MODE_SUBMIT)

KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

MAX_SECTIONS = 30
MAX_FIELDS_PER_SECTION = 50
MAX_TITLE_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 2000
MAX_LABEL_LENGTH = 500
MAX_HELP_TEXT_LENGTH = 1000
# (default, ceiling) for max_length per text type.
TEXT_LIMITS = {"short_text": (200, 500), "long_text": (4000, 10000)}
MAX_OPTIONS = 50
MAX_OPTION_VALUE_LENGTH = 100
MAX_OPTION_LABEL_LENGTH = 200
MAX_RATING_LABEL_LENGTH = 100
RATING_BOUNDS = (0, 10)

DEFINITION_KEYS = {"sections"}
SECTION_KEYS = {"key", "title", "description", "filled_by", "review_stage", "fields"}
COMMON_FIELD_KEYS = {"key", "type", "label", "help_text", "required", "ui"}
TYPE_FIELD_KEYS = {
    "short_text": {"max_length"},
    "long_text": {"max_length"},
    "number": {"min", "max", "integer"},
    "rating": {"scale"},
    "single_choice": {"options"},
    "multi_choice": {"options", "min_selected", "max_selected"},
    "yes_no": set(),
    "date": {"min", "max"},
}


class _Errors:
    def __init__(self):
        self.items = []

    def add(self, path, message):
        self.items.append({"path": path, "message": message})


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _parse_date(value):
    if not isinstance(value, str):
        return None

    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _text(raw, path, max_length, errors, required=True):
    if raw is None:
        if required:
            errors.add(path, "is required")
        return None

    if not isinstance(raw, str):
        errors.add(path, "must be a string")
        return None

    value = raw.strip()
    if not value:
        if required:
            errors.add(path, "is required")
        return None

    if len(value) > max_length:
        errors.add(path, f"must be at most {max_length} characters")

    return value


def _reject_unknown_keys(raw, allowed, path, errors):
    for key in sorted(set(raw) - allowed):
        errors.add(f"{path}.{key}" if path else key, "is not a recognised property")


def validate_definition(definition):
    """Check a form definition and return it normalised.

    Parameters
    ----------
    definition : dict
        ``{"sections": [...]}`` as edited in the form builder.

    Returns
    -------
    tuple of (dict or None, list of dict)
        The normalised definition (defaults filled in, strings trimmed) and the list of
        errors. The definition is ``None`` when it is not an object at all; it may be
        publishable only when the error list is empty.
    """
    errors = _Errors()

    if not isinstance(definition, dict):
        errors.add("definition", "must be an object")
        return None, errors.items

    _reject_unknown_keys(definition, DEFINITION_KEYS, "", errors)

    raw_sections = definition.get("sections")
    if not isinstance(raw_sections, list):
        errors.add("sections", "must be a list")
        return {"sections": []}, errors.items

    if not raw_sections:
        errors.add("sections", "add at least one section")
    if len(raw_sections) > MAX_SECTIONS:
        errors.add("sections", f"must have at most {MAX_SECTIONS} sections")

    section_keys = set()
    field_keys = set()
    sections = []
    for index, raw_section in enumerate(raw_sections):
        section = _section(raw_section, f"sections[{index}]", section_keys, field_keys, errors)
        if section is not None:
            sections.append(section)

    if raw_sections and not any(section["filled_by"] == FILLED_BY_EMPLOYEE for section in sections):
        errors.add("sections", "add at least one section the employee fills")

    return {"sections": sections}, errors.items


def _section(raw, path, section_keys, field_keys, errors):
    if not isinstance(raw, dict):
        errors.add(path, "must be an object")
        return None

    _reject_unknown_keys(raw, SECTION_KEYS, path, errors)

    key = raw.get("key")
    if not isinstance(key, str) or not KEY_PATTERN.match(key):
        errors.add(f"{path}.key", "must be lowercase letters, digits or _ and start with a letter")
    elif key in section_keys:
        errors.add(f"{path}.key", f"duplicates section key {key}")
    else:
        section_keys.add(key)

    section = {
        "key": key,
        "title": _text(raw.get("title"), f"{path}.title", MAX_TITLE_LENGTH, errors),
    }

    description = _text(
        raw.get("description"), f"{path}.description", MAX_DESCRIPTION_LENGTH, errors, False
    )
    if description is not None:
        section["description"] = description

    filled_by = raw.get("filled_by")
    if filled_by not in FILLED_BY:
        errors.add(f"{path}.filled_by", f"must be one of {', '.join(FILLED_BY)}")
    section["filled_by"] = filled_by

    review_stage = raw.get("review_stage")
    if filled_by == FILLED_BY_REVIEWER:
        if review_stage not in REVIEW_STAGES:
            errors.add(f"{path}.review_stage", f"must be one of {', '.join(REVIEW_STAGES)}")
        section["review_stage"] = review_stage
    elif review_stage is not None:
        errors.add(f"{path}.review_stage", "is only allowed on sections a reviewer fills")

    raw_fields = raw.get("fields")
    fields = []
    if not isinstance(raw_fields, list) or not raw_fields:
        errors.add(f"{path}.fields", "add at least one field")
    else:
        if len(raw_fields) > MAX_FIELDS_PER_SECTION:
            errors.add(f"{path}.fields", f"must have at most {MAX_FIELDS_PER_SECTION} fields")
        for index, raw_field in enumerate(raw_fields):
            field = _field(raw_field, f"{path}.fields[{index}]", field_keys, errors)
            if field is not None:
                fields.append(field)

    section["fields"] = fields

    return section


def _field(raw, path, field_keys, errors):
    if not isinstance(raw, dict):
        errors.add(path, "must be an object")
        return None

    field_type = raw.get("type")
    if field_type not in FIELD_TYPES:
        errors.add(f"{path}.type", f"must be one of {', '.join(FIELD_TYPES)}")
        _reject_unknown_keys(raw, COMMON_FIELD_KEYS, path, errors)
        return None

    _reject_unknown_keys(raw, COMMON_FIELD_KEYS | TYPE_FIELD_KEYS[field_type], path, errors)

    key = raw.get("key")
    if not isinstance(key, str) or not KEY_PATTERN.match(key):
        errors.add(f"{path}.key", "must be lowercase letters, digits or _ and start with a letter")
    elif key in field_keys:
        errors.add(f"{path}.key", f"duplicates field key {key}; keys must be unique in a form")
    else:
        field_keys.add(key)

    field = {
        "key": key,
        "type": field_type,
        "label": _text(raw.get("label"), f"{path}.label", MAX_LABEL_LENGTH, errors),
        "required": False,
    }

    required = raw.get("required", False)
    if not isinstance(required, bool):
        errors.add(f"{path}.required", "must be true or false")
    else:
        field["required"] = required

    help_text = _text(
        raw.get("help_text"), f"{path}.help_text", MAX_HELP_TEXT_LENGTH, errors, False
    )
    if help_text is not None:
        field["help_text"] = help_text

    ui = raw.get("ui")
    if ui is not None:
        if isinstance(ui, dict):
            field["ui"] = ui
        else:
            errors.add(f"{path}.ui", "must be an object")

    _TYPE_NORMALISERS[field_type](raw, field, path, errors)

    return field


def _text_properties(raw, field, path, errors):
    default, ceiling = TEXT_LIMITS[field["type"]]
    max_length = raw.get("max_length", default)
    if not _is_int(max_length) or not 1 <= max_length <= ceiling:
        errors.add(f"{path}.max_length", f"must be a whole number from 1 to {ceiling}")
        max_length = default

    field["max_length"] = max_length


def _number_properties(raw, field, path, errors):
    minimum = raw.get("min")
    maximum = raw.get("max")
    integer = raw.get("integer", False)

    if minimum is not None and not _is_number(minimum):
        errors.add(f"{path}.min", "must be a number")
        minimum = None
    if maximum is not None and not _is_number(maximum):
        errors.add(f"{path}.max", "must be a number")
        maximum = None
    if minimum is not None and maximum is not None and minimum > maximum:
        errors.add(f"{path}.min", "must not be greater than max")
    if not isinstance(integer, bool):
        errors.add(f"{path}.integer", "must be true or false")
        integer = False

    field.update({"min": minimum, "max": maximum, "integer": integer})


def _rating_properties(raw, field, path, errors):
    scale = raw.get("scale")
    if not isinstance(scale, dict):
        errors.add(f"{path}.scale", 'is required, e.g. {"min": 1, "max": 5}')
        field["scale"] = {"min": 1, "max": 5}
        return

    _reject_unknown_keys(scale, {"min", "max", "labels"}, f"{path}.scale", errors)

    low, high = RATING_BOUNDS
    minimum = scale.get("min")
    maximum = scale.get("max")
    if not _is_int(minimum) or not _is_int(maximum) or not low <= minimum < maximum <= high:
        errors.add(
            f"{path}.scale", f"min and max must be whole numbers with {low} <= min < max <= {high}"
        )
        field["scale"] = {"min": 1, "max": 5}
        return

    normalised = {"min": minimum, "max": maximum}

    labels = scale.get("labels")
    if labels is not None:
        if not isinstance(labels, dict):
            errors.add(f"{path}.scale.labels", "must map scale values to labels")
        else:
            clean_labels = {}
            for value, label in labels.items():
                label_path = f"{path}.scale.labels.{value}"
                if not str(value).lstrip("-").isdigit() or not minimum <= int(value) <= maximum:
                    errors.add(label_path, f"must be a value from {minimum} to {maximum}")
                    continue
                text = _text(label, label_path, MAX_RATING_LABEL_LENGTH, errors)
                if text is not None:
                    clean_labels[str(int(value))] = text
            normalised["labels"] = clean_labels

    field["scale"] = normalised


def _choice_properties(raw, field, path, errors):
    options = raw.get("options")
    clean_options = []
    if not isinstance(options, list) or not options:
        errors.add(f"{path}.options", "add at least one option")
    elif len(options) > MAX_OPTIONS:
        errors.add(f"{path}.options", f"must have at most {MAX_OPTIONS} options")
    else:
        seen = set()
        for index, option in enumerate(options):
            option_path = f"{path}.options[{index}]"
            if not isinstance(option, dict):
                errors.add(option_path, "must be an object with value and label")
                continue
            _reject_unknown_keys(option, {"value", "label"}, option_path, errors)
            value = _text(
                option.get("value"), f"{option_path}.value", MAX_OPTION_VALUE_LENGTH, errors
            )
            label = _text(
                option.get("label"), f"{option_path}.label", MAX_OPTION_LABEL_LENGTH, errors
            )
            if value is not None and value in seen:
                errors.add(f"{option_path}.value", f"duplicates option {value}")
            elif value is not None:
                seen.add(value)
                clean_options.append({"value": value, "label": label})

    field["options"] = clean_options

    if field["type"] != "multi_choice":
        return

    min_selected = raw.get("min_selected")
    max_selected = raw.get("max_selected")
    for name, value in (("min_selected", min_selected), ("max_selected", max_selected)):
        if value is not None and (not _is_int(value) or value < 0):
            errors.add(f"{path}.{name}", "must be a whole number of at least 0")
    if _is_int(min_selected) and _is_int(max_selected) and min_selected > max_selected:
        errors.add(f"{path}.min_selected", "must not be greater than max_selected")
    if _is_int(max_selected) and clean_options and max_selected > len(clean_options):
        errors.add(f"{path}.max_selected", "must not exceed the number of options")

    field["min_selected"] = min_selected if _is_int(min_selected) else None
    field["max_selected"] = max_selected if _is_int(max_selected) else None


def _no_properties(raw, field, path, errors):
    return None


def _date_properties(raw, field, path, errors):
    bounds = {}
    for name in ("min", "max"):
        value = raw.get(name)
        if value is None:
            bounds[name] = None
            continue
        if _parse_date(value) is None:
            errors.add(f"{path}.{name}", "must be a date as YYYY-MM-DD")
            bounds[name] = None
        else:
            bounds[name] = value

    if bounds["min"] and bounds["max"] and bounds["min"] > bounds["max"]:
        errors.add(f"{path}.min", "must not be after max")

    field.update(bounds)


_TYPE_NORMALISERS = {
    "short_text": _text_properties,
    "long_text": _text_properties,
    "number": _number_properties,
    "rating": _rating_properties,
    "single_choice": _choice_properties,
    "multi_choice": _choice_properties,
    "yes_no": _no_properties,
    "date": _date_properties,
}


def fields_for_stage(definition, stage):
    """Return ``{field_key: field}`` for the sections filled at *stage*.

    Parameters
    ----------
    definition : dict
        A normalised (published) definition.
    stage : str
        ``employee``, ``lead`` or ``manager``.
    """
    fields = {}
    for section in definition.get("sections", []):
        if stage == FILLED_BY_EMPLOYEE:
            matches = section.get("filled_by") == FILLED_BY_EMPLOYEE
        else:
            matches = (
                section.get("filled_by") == FILLED_BY_REVIEWER
                and section.get("review_stage") == stage
            )
        if matches:
            for field in section.get("fields", []):
                fields[field["key"]] = field

    return fields


def validate_answers(definition, answers, stage, mode):
    """Check one stage's answers against a published definition.

    Parameters
    ----------
    definition : dict
        A normalised (published) definition.
    answers : dict
        ``{field_key: value}``; ``None`` clears a value.
    stage : str
        ``employee``, ``lead`` or ``manager``; only that stage's fields may be answered.
    mode : str
        ``draft`` checks types and limits; ``submit`` also requires every required field
        of the stage to have a value.

    Returns
    -------
    tuple of (dict, list of dict)
        Normalised answers (trimmed text, empty values as ``None``) and the errors found.
    """
    errors = _Errors()

    if stage not in ANSWER_STAGES:
        errors.add("stage", f"must be one of {', '.join(ANSWER_STAGES)}")
        return {}, errors.items
    if mode not in MODES:
        errors.add("mode", f"must be one of {', '.join(MODES)}")
        return {}, errors.items
    if not isinstance(answers, dict):
        errors.add("answers", "must be an object keyed by field")
        return {}, errors.items

    stage_fields = fields_for_stage(definition, stage)
    other_stage = {}
    for other in ANSWER_STAGES:
        if other != stage:
            for key in fields_for_stage(definition, other):
                other_stage[key] = other

    normalised = {}
    for key, value in answers.items():
        field = stage_fields.get(key)
        if field is None:
            if key in other_stage:
                errors.add(key, f"is filled at the {other_stage[key]} stage, not {stage}")
            else:
                errors.add(key, "is not a field of this form")
            continue

        normalised[key] = _answer(field, value, key, mode, errors)

    if mode == MODE_SUBMIT:
        for key, field in stage_fields.items():
            if field.get("required") and _is_empty(normalised.get(key)):
                errors.add(key, "is required")

    return normalised, errors.items


def _is_empty(value):
    return value is None or value == "" or value == []


def _answer(field, value, path, mode, errors):
    if value is None:
        return None

    field_type = field["type"]

    if field_type in ("short_text", "long_text"):
        if not isinstance(value, str):
            errors.add(path, "must be text")
            return None
        text = value.strip()
        if len(text) > field["max_length"]:
            errors.add(path, f"must be at most {field['max_length']} characters")
        return text or None

    if field_type == "number":
        if not _is_number(value):
            errors.add(path, "must be a number")
            return None
        if field.get("integer") and not float(value).is_integer():
            errors.add(path, "must be a whole number")
        if field.get("min") is not None and value < field["min"]:
            errors.add(path, f"must be at least {field['min']}")
        if field.get("max") is not None and value > field["max"]:
            errors.add(path, f"must be at most {field['max']}")
        return value

    if field_type == "rating":
        scale = field["scale"]
        if not _is_int(value) or not scale["min"] <= value <= scale["max"]:
            errors.add(path, f"must be a whole number from {scale['min']} to {scale['max']}")
            return None
        return value

    if field_type == "single_choice":
        allowed = {option["value"] for option in field["options"]}
        if not isinstance(value, str) or value not in allowed:
            errors.add(path, "must be one of the listed options")
            return None
        return value

    if field_type == "multi_choice":
        return _multi_choice_answer(field, value, path, mode, errors)

    if field_type == "yes_no":
        if not isinstance(value, bool):
            errors.add(path, "must be true or false")
            return None
        return value

    if field_type == "date":
        parsed = _parse_date(value)
        if parsed is None:
            errors.add(path, "must be a date as YYYY-MM-DD")
            return None
        if field.get("min") and value < field["min"]:
            errors.add(path, f"must be on or after {field['min']}")
        if field.get("max") and value > field["max"]:
            errors.add(path, f"must be on or before {field['max']}")
        return value

    errors.add(path, "has an unsupported type")
    return None


def _multi_choice_answer(field, value, path, mode, errors):
    allowed = {option["value"] for option in field["options"]}
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        errors.add(path, "must be a list of options")
        return None
    if len(set(value)) != len(value):
        errors.add(path, "must not repeat an option")
    if any(item not in allowed for item in value):
        errors.add(path, "must only contain the listed options")

    if field.get("max_selected") is not None and len(value) > field["max_selected"]:
        errors.add(path, f"must select at most {field['max_selected']} options")
    # A draft may be partly filled in; the minimum only applies once submitted.
    if mode == MODE_SUBMIT and value and field.get("min_selected") is not None:
        if len(value) < field["min_selected"]:
            errors.add(path, f"must select at least {field['min_selected']} options")

    return value
