from io import BytesIO
from urllib.error import HTTPError

import pytest
from flask import Flask

from app.storage_imports import storage


@pytest.mark.parametrize("code, hint", [(400, "bucket settings"), (401, "SUPABASE_STORAGE_SECRET_KEY"), (403, "permissions"), (404, "SUPABASE_URL"), (429, "limit"), (503, "unavailable")])
def test_storage_errors_report_status_without_exposing_provider_body(monkeypatch, code, hint):
    app = Flask(__name__)
    app.config.update(SUPABASE_URL="https://example.supabase.co", SUPABASE_STORAGE_SECRET_KEY="private-secret", IMPORT_MAX_FILE_BYTES=1000)
    def fail(*args, **kwargs):
        raise HTTPError("https://example.supabase.co/private", code, "sensitive-provider-message", {}, BytesIO(b'{"message":"private-secret"}'))
    monkeypatch.setattr(storage, "urlopen", fail)
    with app.app_context(), pytest.raises(storage.StorageUnavailable) as error:
        storage._request("PUT", "/bucket/hph-imports", {})
    message = str(error.value)
    assert f"HTTP {code}, PUT" in message
    assert hint in message
    assert "private-secret" not in message
    assert "sensitive-provider-message" not in message
    assert "example.supabase.co" not in message
