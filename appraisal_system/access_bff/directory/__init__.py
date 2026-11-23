from flask_smorest import Blueprint


bp = Blueprint(
    "directory",
    __name__,
    url_prefix="/api/directory",
    description="Read-only directory of users and projects for HPH microservices",
)

from appraisal_system.access_bff.directory import routes  # noqa: E402,F401
