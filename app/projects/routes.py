from flask import g
from flask.views import MethodView
from flask_smorest import abort
from marshmallow import Schema, fields, pre_load, validate
from sqlalchemy.exc import IntegrityError

from app.auth import has_feature, require_feature
from app.extensions import db
from app.projects import bp
from app.responses import envelope_schema
from app.users.models import Project, User

# Existing reporting logic identifies these projects by name.
SYSTEM_PROJECTS = {"RCM", "CODING"}

class ProjectSchema(Schema):
    id = fields.Integer(dump_only=True)
    name = fields.String(required=True, validate=validate.Length(min=1, max=64))
    user_count = fields.Integer(dump_only=True)
    is_system = fields.Boolean(dump_only=True)

    @pre_load
    def trim_name(self, data, **kwargs):
        if isinstance(data, dict) and isinstance(data.get("name"), str):
            data = {**data, "name": data["name"].strip()}
        return data

ProjectEnvelope = envelope_schema("ProjectEnvelope", fields.Nested(ProjectSchema))
ProjectListEnvelope = envelope_schema("ProjectListEnvelope", fields.List(fields.Nested(ProjectSchema)))

def serialize(project, count=None):
    return {"id": project.id, "name": project.name,
            "user_count": count if count is not None else User.query.filter_by(project_id=project.id).count(),
            "is_system": project.name in SYSTEM_PROJECTS}

def save(project, name):
    duplicate = Project.query.filter(db.func.lower(Project.name) == name.lower(), Project.id != (project.id or 0)).first()
    if duplicate:
        abort(409, message="A project with this name already exists.")
    project.name = name
    db.session.add(project)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        abort(409, message="A project with this name already exists.")

@bp.route("/projects/options")
class ProjectOptions(MethodView):
    @bp.response(200, ProjectListEnvelope)
    def get(self):
        user = getattr(g, "user", None)
        if not (has_feature(user, "user_management") or has_feature(user, "project_management")):
            abort(403, message="Project options require user management or project management access.")
        return {"status": 200, "message": "Projects retrieved successfully.",
                "data": [{"id": p.id, "name": p.name} for p in Project.query.order_by(Project.name).all()]}

@bp.route("/projects")
class Projects(MethodView):
    @require_feature("project_management", access="read")
    @bp.response(200, ProjectListEnvelope)
    def get(self):
        rows = db.session.query(Project, db.func.count(User.id)).outerjoin(User, User.project_id == Project.id).group_by(Project.id).order_by(Project.name).all()
        return {"status": 200, "message": "Projects retrieved successfully.", "data": [serialize(p, count) for p, count in rows]}

    @require_feature("project_management", access="write")
    @bp.arguments(ProjectSchema)
    @bp.response(201, ProjectEnvelope)
    def post(self, data):
        project = Project()
        save(project, data["name"])
        return {"status": 201, "message": "Project created successfully.", "data": serialize(project)}

@bp.route("/projects/<int:project_id>")
class ProjectDetail(MethodView):
    @require_feature("project_management", access="write")
    @bp.arguments(ProjectSchema)
    @bp.response(200, ProjectEnvelope)
    def patch(self, data, project_id):
        project = Project.query.get_or_404(project_id)
        if project.name in SYSTEM_PROJECTS:
            abort(409, message="System project names are required by reporting and cannot be changed.")
        save(project, data["name"])
        return {"status": 200, "message": "Project updated successfully.", "data": serialize(project)}

    @require_feature("project_management", access="write")
    @bp.response(200, ProjectEnvelope)
    def delete(self, project_id):
        project = Project.query.get_or_404(project_id)
        if project.name in SYSTEM_PROJECTS or User.query.filter_by(project_id=project.id).first():
            abort(409, message="System projects and projects assigned to users cannot be deleted.")
        result = serialize(project)
        db.session.delete(project)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            abort(409, message="This project is in use and cannot be deleted.")
        return {"status": 200, "message": "Project deleted successfully.", "data": result}
