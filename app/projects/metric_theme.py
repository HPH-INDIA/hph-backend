"""Shared metric colors stored with the authenticated user's project."""
from datetime import datetime, timezone
from functools import wraps

from flask import g
from flask.views import MethodView
from flask_smorest import abort
from marshmallow import Schema, fields, validate

from app.extensions import db
from app.projects import bp
from app.responses import envelope_schema

DEFAULT_METRIC_THEME = {
    "kairon": "#312e81", "manual": "#c2410c",
    "adjusted": "#0f766e", "target": "#9333ea",
}

class MetricThemeSchema(Schema):
    kairon = fields.String(required=True, validate=validate.Regexp(r"^#[0-9a-fA-F]{6}$"))
    manual = fields.String(required=True, validate=validate.Regexp(r"^#[0-9a-fA-F]{6}$"))
    adjusted = fields.String(required=True, validate=validate.Regexp(r"^#[0-9a-fA-F]{6}$"))
    target = fields.String(required=True, validate=validate.Regexp(r"^#[0-9a-fA-F]{6}$"))

class ProjectThemeSchema(Schema):
    theme = fields.Nested(MetricThemeSchema, required=True)
    configured = fields.Boolean(required=True)

ProjectThemeEnvelope = envelope_schema("ProjectThemeEnvelope", fields.Nested(ProjectThemeSchema))

def coding_user():
    user = getattr(g, "user", None)
    if user is None or not user.is_active:
        abort(401, message="An active account is required.")
    if user.project is None or user.project.name.strip().upper() != "CODING":
        abort(403, message="This theme belongs to the Coding project.")
    return user

def theme_response(project, message):
    return {"status": 200, "message": message, "data": {
        "theme": project.metric_theme if project.metric_theme is not None else dict(DEFAULT_METRIC_THEME),
        "configured": project.metric_theme is not None,
    }}

def require_theme_manager(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if coding_user().role.role_type.code != "manager":
            abort(403, message="Only Coding project managers can change this theme.")
        return view(*args, **kwargs)
    return wrapped

@bp.route("/project-metric-theme")
class ProjectMetricTheme(MethodView):
    @bp.response(200, ProjectThemeEnvelope)
    def get(self):
        return theme_response(coding_user().project, "Project theme loaded.")

    # Check permission before parsing the body, including unknown fields.
    @require_theme_manager
    @bp.arguments(MetricThemeSchema)
    @bp.response(200, ProjectThemeEnvelope)
    def put(self, theme):
        user = coding_user()
        user.project.metric_theme = {key: value.lower() for key, value in theme.items()}
        user.project.metric_theme_updated_at = datetime.now(timezone.utc)
        user.project.metric_theme_updated_by_id = user.id
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return theme_response(user.project, "Project theme saved.")
