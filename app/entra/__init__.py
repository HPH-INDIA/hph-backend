"""Server-side Microsoft Entra authentication; browser tokens never enter the SPA."""
from flask import Blueprint
bp = Blueprint("entra", __name__, url_prefix="/auth")
from app.entra import routes  # noqa: E402,F401
