"""Small server-only Supabase Storage REST adapter; never exposes its secret."""
import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
from flask import current_app


class StorageUnavailable(RuntimeError):
    pass


def _request(method, path, payload=None, *, missing_ok=False, binary=False):
    origin = current_app.config["SUPABASE_URL"].rstrip("/")
    secret = current_app.config["SUPABASE_STORAGE_SECRET_KEY"]
    if not origin or not secret:
        raise StorageUnavailable("Supabase Storage is not configured.")
    headers = {"Authorization": f"Bearer {secret}", "apikey": secret}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode()
    try:
        with urlopen(Request(origin + "/storage/v1" + path, data=body, headers=headers, method=method), timeout=30) as response:
            limit = current_app.config["IMPORT_MAX_FILE_BYTES"]
            data = response.read(limit + 1) if binary else response.read(1_000_000)
            if binary and len(data) > limit:
                raise ValueError("The uploaded file exceeds the configured size limit.")
            return data if binary else json.loads(data or b"{}")
    except HTTPError as exc:
        if missing_ok and exc.code in (400, 404):
            # Storage reports missing objects as 400 with a 404 statusCode.
            try:
                detail = json.loads(exc.read(10_000))
            except (ValueError, OSError):
                detail = {}
            if exc.code == 404 or str(detail.get("statusCode")) == "404":
                return None
        raise StorageUnavailable("Supabase Storage request failed. Please retry.") from None
    except (URLError, TimeoutError, OSError):
        raise StorageUnavailable("Supabase Storage is temporarily unavailable.") from None


def _path(path):
    return quote(current_app.config["SUPABASE_IMPORT_BUCKET"], safe="") + "/" + quote(path, safe="/")


def sign_upload(path):
    result = _request("POST", "/object/upload/sign/" + _path(path), {})
    url = result["url"]
    origin = current_app.config["SUPABASE_URL"].rstrip("/")
    if url.startswith("/"):
        url = origin + "/storage/v1" + url
    if urlsplit(url).netloc != urlsplit(origin).netloc:
        raise StorageUnavailable("Unexpected signed upload URL origin.")
    return url


def exists(path):
    return _request("GET", "/object/info/authenticated/" + _path(path), missing_ok=True)


def download(path):
    return _request("GET", "/object/authenticated/" + _path(path), binary=True)


def delete(path):
    return _request("DELETE", "/object/" + quote(current_app.config["SUPABASE_IMPORT_BUCKET"], safe=""), {"prefixes": [path]})
