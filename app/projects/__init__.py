from flask_smorest import Blueprint

bp = Blueprint("projects", __name__, url_prefix="/api", description="Project management")
from app.projects import routes  # noqa: E402,F401

from app.projects import metric_theme  # noqa: E402,F401
