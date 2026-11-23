from flask import Blueprint


# A plain Flask blueprint: these routes forward whatever the services' own OpenAPI specs define,
# so they carry no schemas of their own.
bp = Blueprint("gateway", __name__)

from appraisal_system.access_bff.gateway import routes  # noqa: E402,F401
