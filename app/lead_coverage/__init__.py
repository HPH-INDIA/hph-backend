from flask_smorest import Blueprint

bp = Blueprint(
    "lead_coverage", __name__, url_prefix="/api/lead-coverage",
    description="Time-limited, approved coverage of a lead's team.",
)

from app.lead_coverage import routes  # noqa: E402,F401
