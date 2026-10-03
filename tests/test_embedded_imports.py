import threading
from contextlib import contextmanager

from flask import current_app

from app.storage_imports import embedded
from app.storage_imports.services import kind_lock
from test_kairon import _login_manager
from test_storage_imports import fake_storage, make_job, submit, _sample_row


def test_disabled_mode_does_not_start_a_thread(app, monkeypatch):
    monkeypatch.setitem(app.config, "IMPORT_EMBEDDED_PROCESSING", False)
    monkeypatch.setattr(embedded.threading, "Thread", lambda **kw: (_ for _ in ()).throw(AssertionError("Unexpected thread")))
    with app.app_context():
        embedded.kick_import_processing()


def test_kick_returns_without_waiting_and_starts_only_one_thread(app, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []
    @contextmanager
    def lock(kind):
        assert kind == "embedded"
        yield True
    def process():
        calls.append(current_app._get_current_object())
        entered.set()
        assert release.wait(5)
    monkeypatch.setitem(app.config, "IMPORT_EMBEDDED_PROCESSING", True)
    monkeypatch.setattr(embedded, "kind_lock", lock)
    monkeypatch.setattr(embedded, "run_once", process)
    monkeypatch.setattr(embedded, "cleanup_replaced_files", lambda: None)
    try:
        with app.app_context():
            embedded.kick_import_processing()
            assert entered.wait(5)
            first = app.extensions["embedded_imports"]["thread"]
            embedded.kick_import_processing()
            assert app.extensions["embedded_imports"]["thread"] is first
            assert calls == [app]
    finally:
        release.set()
        app.extensions["embedded_imports"]["thread"].join(5)
    # A new pass is possible after the thread exits (or a service restarts).
    with app.app_context():
        embedded.kick_import_processing()
    app.extensions["embedded_imports"]["thread"].join(5)
    assert calls == [app, app]


def test_embedded_lock_serializes_both_types(app):
    with kind_lock("embedded") as first:
        assert first
        with kind_lock("embedded") as second:
            assert not second


def test_embedded_worker_processes_queued_import(app, manager_user, fake_storage, monkeypatch):
    from app.extensions import db
    from app.storage_imports.models import StorageImport
    objects, _ = fake_storage
    job, _ = make_job(manager_user)
    submit(job, [_sample_row(mbi="embedded-synthetic")], objects)
    import_id = job.id
    monkeypatch.setitem(app.config, "IMPORT_EMBEDDED_PROCESSING", True)
    embedded.kick_import_processing()
    thread = app.extensions["embedded_imports"]["thread"]
    thread.join(15)
    assert not thread.is_alive()
    db.session.expire_all()
    job = db.session.get(StorageImport, import_id)
    assert job.status == "completed", job.error


def test_progress_request_kicks_only_authorized_active_import(api_client, app, manager_user, employee_user, fake_storage, monkeypatch):
    from test_storage_imports import make_job
    from app.extensions import db
    import app.storage_imports.routes as routes
    calls = []
    monkeypatch.setattr(routes, "kick_import_processing", lambda: calls.append(True))
    job, _ = make_job(manager_user)
    _login_manager(api_client, manager_user)
    status, _ = api_client.get(f"/api/file-imports/{job.id}")
    assert status == 200
    assert calls == [True]
    job.status = "completed"
    db.session.commit()
    api_client.get(f"/api/file-imports/{job.id}")
    assert calls == [True]
    job.uploaded_by_id = employee_user.id
    job.status = "queued"
    db.session.commit()
    status, _ = api_client.get(f"/api/file-imports/{job.id}")
    assert status == 403
    assert calls == [True]
