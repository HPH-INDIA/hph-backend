"""Isolated database/HTTP tests; no deployment credentials or business data."""
import os
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ["EMAIL_VALIDATE_CONFIG"] = "false"
os.environ.setdefault("SECRET_KEY", "isolated-test-secret")
import tempfile
import unittest
from pathlib import Path

from flask import Flask, g
from flask_smorest import Api
from werkzeug.exceptions import HTTPException
from app.extensions import db
from app.roles.models import Role, RoleType
from app.users.models import Project, User
from app.projects import bp
from app.projects.metric_theme import DEFAULT_METRIC_THEME

class ProjectThemeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.uri = "sqlite:///" + str(Path(self.temp.name) / "themes.sqlite")
        self.user_id = None
        self.app = self.make_app()
        with self.app.app_context():
            db.create_all()
            projects = [Project(name="CODING"), Project(name="RCM")]
            db.session.add_all(projects)
            db.session.flush()
            self.ids = {}
            for index, code in enumerate(("manager", "lead", "employee")):
                role_type = RoleType(code=code, label=code, hierarchy_rank=index)
                role = Role(title=code, role_type=role_type)
                db.session.add(role)
                db.session.flush()
                user = User(email=code+"@test.local", emp_id=code, first_name=code,
                            last_name="Test", role=role, project=projects[0],
                            password_hash="unused", is_active=True)
                db.session.add(user)
                db.session.flush()
                self.ids[code] = user.id
            self.project_id = projects[0].id
            self.other_id = projects[1].id
            db.session.commit()
        self.client = self.app.test_client()

    def make_app(self):
        app = Flask(__name__)
        app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=self.uri,
                          API_TITLE="Test", API_VERSION="v1", OPENAPI_VERSION="3.0.3")
        db.init_app(app)
        Api(app).register_blueprint(bp)
        @app.before_request
        def auth():
            g.user = db.session.get(User, self.user_id) if self.user_id else None
        @app.errorhandler(HTTPException)
        def error(err):
            return {"status": err.code}, err.code
        return app

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        self.temp.cleanup()

    def test_persistence_shared_reads_and_audit(self):
        self.user_id = self.ids["manager"]
        initial = self.client.get("/api/project-metric-theme").json["data"]
        self.assertEqual(initial, {"theme": DEFAULT_METRIC_THEME, "configured": False})
        theme = {**DEFAULT_METRIC_THEME, "manual": "#ABC123"}
        response = self.client.put("/api/project-metric-theme", json=theme)
        self.assertEqual(response.status_code, 200)
        expected = {**theme, "manual": "#abc123"}
        with self.app.app_context():
            project = db.session.get(Project, self.project_id)
            self.assertEqual(project.metric_theme_updated_by_id, self.user_id)
            self.assertIsNotNone(project.metric_theme_updated_at)
        # A fresh application instance/connection reads the persisted value.
        fresh_client = self.make_app().test_client()
        for role in ("employee", "lead", "manager"):
            self.user_id = self.ids[role]
            self.assertEqual(fresh_client.get("/api/project-metric-theme").json["data"],
                             {"theme": expected, "configured": True})

    def test_permissions_and_project_boundary(self):
        self.assertEqual(self.client.get("/api/project-metric-theme").status_code, 401)
        for role in ("employee", "lead"):
            self.user_id = self.ids[role]
            self.assertEqual(self.client.put("/api/project-metric-theme", json=DEFAULT_METRIC_THEME).status_code, 403)
        self.user_id = self.ids["manager"]
        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            user.project_id = self.other_id
            db.session.commit()
        self.assertEqual(self.client.get("/api/project-metric-theme").status_code, 403)
        self.assertEqual(self.client.put("/api/project-metric-theme", json=DEFAULT_METRIC_THEME).status_code, 403)
        with self.app.app_context():
            self.assertIsNone(db.session.get(Project, self.other_id).metric_theme)
            user = db.session.get(User, self.user_id)
            user.project_id = self.project_id
            user.is_active = False
            db.session.commit()
        self.assertEqual(self.client.get("/api/project-metric-theme").status_code, 401)

    def test_invalid_write_preserves_saved_theme(self):
        self.user_id = self.ids["manager"]
        self.client.put("/api/project-metric-theme", json=DEFAULT_METRIC_THEME)
        for body in ({}, {**DEFAULT_METRIC_THEME, "target": "red"},
                     {**DEFAULT_METRIC_THEME, "project_id": self.other_id},
                     {**DEFAULT_METRIC_THEME, "kairon": None}):
            self.assertEqual(self.client.put("/api/project-metric-theme", json=body).status_code, 422)
            self.assertEqual(self.client.get("/api/project-metric-theme").json["data"]["theme"], DEFAULT_METRIC_THEME)

if __name__ == "__main__":
    unittest.main()
