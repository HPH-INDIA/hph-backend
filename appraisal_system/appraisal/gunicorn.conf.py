import os


bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "30"))
accesslog = "-"

# Callers are identified by the `caller_id` header, which gunicorn >= 22 drops by default
# because of the underscore. It is safe to map: every request's caller_id is checked against
# its signed bearer token (common/connexion_app.py).
header_map = "dangerous"
