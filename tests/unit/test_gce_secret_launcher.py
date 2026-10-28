"""Exercise the production launcher without cloud access, Flask, or a database."""
import base64
import io
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

LAUNCHER = Path(__file__).resolve().parents[2] / 'deploy/gce/run-with-secrets.py'


class SecretLauncherTests(unittest.TestCase):
    def run_launcher(self, certificate=True, fail_access=False, symlink=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = root / 'private-key.pem'
            settings = root / 'settings.json'
            references = root / 'secret-refs.json'
            settings.write_text(json.dumps({'ENTRA_ENABLED': 'false'}))
            refs = {'SECRET_KEY': 'projects/test/secrets/session/versions/1'}
            payloads = {'session': 'synthetic-session-secret'}
            if certificate:
                refs['ENTRA_PRIVATE_KEY_PEM'] = 'projects/test/secrets/certificate/versions/1'
                payloads['certificate'] = 'synthetic-private-pem'
            references.write_text(json.dumps(refs))
            if symlink:
                target = root / 'untouched'
                target.write_text('unchanged')
                key.symlink_to(target)

            def mapped_path(value):
                return {
                    '/opt/hph/settings.json': settings,
                    '/opt/hph/secret-refs.json': references,
                    '/run/hph-entra/private-key.pem': key,
                }[value]

            def response(request, timeout):
                if 'metadata.google.internal' in request.full_url:
                    return io.BytesIO(b'{"access_token":"synthetic-token"}')
                if fail_access:
                    raise OSError('Secret access denied')
                secret = request.full_url.split('/secrets/')[1].split('/')[0]
                return io.BytesIO(json.dumps({'payload': {'data': base64.b64encode(payloads[secret].encode()).decode()}}).encode())

            captured = {}

            def exec_gunicorn(path, args):
                captured.update(path=path, args=args, environment=dict(os.environ))
                if key.exists():
                    captured.update(key=key.read_text(), mode=key.stat().st_mode & 0o777)

            with patch('pathlib.Path', side_effect=mapped_path), patch('urllib.request.urlopen', side_effect=response), patch.dict(os.environ, {}, clear=True), patch('os.execv', side_effect=exec_gunicorn):
                if fail_access or symlink:
                    with self.assertRaises(OSError):
                        runpy.run_path(str(LAUNCHER))
                    self.assertEqual(captured, {})
                    if symlink:
                        self.assertEqual(target.read_text(), 'unchanged')
                else:
                    runpy.run_path(str(LAUNCHER))
            return captured

    def test_certificate_is_private_and_not_passed_in_environment(self):
        result = self.run_launcher()
        self.assertEqual(result['key'], 'synthetic-private-pem')
        self.assertEqual(result['mode'], 0o600)
        self.assertNotIn('ENTRA_PRIVATE_KEY_PEM', result['environment'])
        self.assertTrue(result['environment']['ENTRA_PRIVATE_KEY_FILE'].endswith('private-key.pem'))
        self.assertEqual(result['environment']['ENTRA_ENABLED'], 'false')
        self.assertEqual(result['environment']['SECRET_KEY'], 'synthetic-session-secret')
        self.assertEqual(result['path'], '/opt/hph/venv/bin/gunicorn')

    def test_password_only_configuration_still_launches(self):
        result = self.run_launcher(certificate=False)
        self.assertNotIn('ENTRA_PRIVATE_KEY_FILE', result['environment'])
        self.assertEqual(result['environment']['SECRET_KEY'], 'synthetic-session-secret')

    def test_secret_access_failure_prevents_launch(self):
        self.run_launcher(fail_access=True)

    def test_key_symlink_is_rejected_without_overwriting_target(self):
        self.run_launcher(symlink=True)


if __name__ == '__main__':
    unittest.main()
