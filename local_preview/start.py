"""Run the existing local API with isolated, disk-backed test theme settings."""
import os
import sys
from pathlib import Path
from dotenv import dotenv_values

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent
configured = dotenv_values(SOURCE / '.env.render')
os.environ.update({key:value for key,value in configured.items() if value is not None})
os.environ.update({'SESSION_COOKIE_SECURE':'false','FRONTEND_LOGIN_URL':'http://127.0.0.1:55175/login','CORS_ALLOWED_ORIGINS':'http://127.0.0.1:55175','CELERY_TASK_ALWAYS_EAGER':'true','CELERY_BROKER_URL':'memory://','CELERY_RESULT_BACKEND':'cache+memory://','IMPORT_EMBEDDED_PROCESSING':'true','EMAIL_SUPPRESS_SEND':'true','EMAIL_VALIDATE_CONFIG':'false','LOG_DECRYPTED_PAYLOADS':'false','WEB_CONCURRENCY':'1'})
sys.path.insert(0, str(SOURCE))
from app import create_app
from metric_theme import register_metric_theme
app = create_app()
register_metric_theme(app, str(HERE / 'data' / 'themes.sqlite3'))
app.run(host='127.0.0.1', port=8084, use_reloader=False)
