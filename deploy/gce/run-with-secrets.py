"""Load pinned Google secrets; materialize the Entra key only in systemd's /run directory."""
import base64
import json
import os
from pathlib import Path
import urllib.request

settings = json.loads(Path('/opt/hph/settings.json').read_text())
references = json.loads(Path('/opt/hph/secret-refs.json').read_text())
request = urllib.request.Request(
    'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token',
    headers={'Metadata-Flavor': 'Google'},
)
token = json.load(urllib.request.urlopen(request, timeout=15))['access_token']
for key, reference in references.items():
    request = urllib.request.Request(
        'https://secretmanager.googleapis.com/v1/' + reference + ':access',
        headers={'Authorization': 'Bearer ' + token},
    )
    result = json.load(urllib.request.urlopen(request, timeout=15))
    settings[key] = base64.b64decode(result['payload']['data']).decode()

# The service owns this mode-0700 directory, created by RuntimeDirectory=hph-entra.
private_key = settings.pop('ENTRA_PRIVATE_KEY_PEM', None)
if private_key:
    key_path = Path('/run/hph-entra/private-key.pem')
    descriptor = os.open(
        str(key_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600,
    )
    with os.fdopen(descriptor, 'w') as key_file:
        os.fchmod(key_file.fileno(), 0o600)
        key_file.write(private_key)
    settings['ENTRA_PRIVATE_KEY_FILE'] = str(key_path)
    del private_key
os.environ.update(settings)
os.execv('/opt/hph/venv/bin/gunicorn', [
    'gunicorn', '--config', 'gunicorn.conf.py', '--bind', '127.0.0.1:8080', 'run:app',
])
