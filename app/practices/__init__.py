from flask_smorest import Blueprint

bp = Blueprint("practices", __name__, url_prefix="/api", description="Practices master list and assignments")

from app.practices import routes  # noqa: E402,F401
