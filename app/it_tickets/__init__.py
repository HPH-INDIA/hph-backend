from flask_smorest import Blueprint

bp = Blueprint("it_tickets", __name__, url_prefix="/api", description="CODING IT (Jira) tickets")

from app.it_tickets import routes  # noqa: E402,F401
