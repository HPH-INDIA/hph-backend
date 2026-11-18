"""Run `flask db upgrade` on the VM with the same settings as the web service.

Loads /opt/hph/settings.json and the pinned Secret Manager values exactly like
run-with-secrets.py, then runs the Alembic upgrade instead of starting
Gunicorn. Nothing is printed or written to disk. Microsoft sign-in is turned
off for this process only, so the Entra private key is never materialised.

Run as the service user from /opt/hph:
    sudo -u hph /opt/hph/venv/bin/python /opt/hph/migrate-with-secrets.py
"""
import base64
import json
import os
from pathlib import Path
import urllib.request

APP_DIR = Path("/opt/hph")
settings = json.loads((APP_DIR / "settings.json").read_text())
references = json.loads((APP_DIR / "secret-refs.json").read_text())
request = urllib.request.Request(
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
    headers={"Metadata-Flavor": "Google"},
)
token = json.load(urllib.request.urlopen(request, timeout=15))["access_token"]
for key, reference in references.items():
    if key == "ENTRA_PRIVATE_KEY_PEM":
        continue
    request = urllib.request.Request(
        "https://secretmanager.googleapis.com/v1/" + reference + ":access",
        headers={"Authorization": "Bearer " + token},
    )
    result = json.load(urllib.request.urlopen(request, timeout=15))
    settings[key] = base64.b64decode(result["payload"]["data"]).decode()

settings["ENTRA_ENABLED"] = "false"
os.environ.update(settings)
os.chdir(APP_DIR)
os.execv(str(APP_DIR / "venv/bin/python"), ["python", "-m", "flask", "--app", "run:app", "db", "upgrade"])
