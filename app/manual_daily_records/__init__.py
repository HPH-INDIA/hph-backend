from flask_smorest import Blueprint

bp = Blueprint(
    "manual_daily_records",
    __name__,
    url_prefix="/api",
    description="Manual daily production/attendance records: self-entry, direct-lead review, and manager oversight.",
)

from app.manual_daily_records import routes  # noqa: E402,F401
