from flask_smorest import Blueprint

bp = Blueprint("storage_imports", __name__, url_prefix="/api", description="Durable file imports")
from app.storage_imports import routes  # noqa: E402,F401
