import base64
import hashlib
import json
import os
import uuid

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from werkzeug.exceptions import HTTPException
from app.extensions import db
from app.encryption.crypto import unwrap_key
from app.kairon.models import KaironChartRecord, KaironChartHistory, KaironImportChunk
from app.storage_imports.models import StorageImport, StorageImportSlot
from app.storage_imports.services import prepare, upload_complete, process_job, run_once, kind_lock, retry
from app.storage_imports import storage
from test_kairon import _get_or_create_user, _sample_row, _login_manager


@pytest.fixture
def fake_storage(monkeypatch):
    objects = {}
    signed = []
    monkeypatch.setattr(storage, "sign_upload", lambda path: signed.append(path) or f"https://test.supabase.co/storage/v1/object/upload/sign/hph-imports/{path}?token=test")
    monkeypatch.setattr(storage, "exists", lambda path: {"size": len(objects[path]), "metadata": None} if path in objects else None)
    monkeypatch.setattr(storage, "download", lambda path: objects[path])
    monkeypatch.setattr(storage, "delete", lambda path: objects.pop(path, None))
    return objects, signed


def make_job(manager, kind="kairon", count=1):
    data = {"kind": kind, "source_filename": "test.csv", "source_checksum": "a" * 64,
            "total_rows": count, "request_id": uuid.uuid4()}
    result = prepare(data, manager.id)
    return db.session.get(StorageImport, result["id"]), data


def encrypt(job, rows):
    key = AESGCM(unwrap_key(job.wrapped_key))
    lines = []
    for offset in range(0, len(rows), 1000):
        nonce = os.urandom(12)
        aad = f"hph-import:v1:{job.id}:{offset // 1000}".encode()
        lines.append(base64.b64encode(nonce + key.encrypt(nonce, json.dumps(rows[offset:offset+1000]).encode(), aad)))
    return b"\n".join(lines) + b"\n"


def submit(job, rows, objects):
    contents = encrypt(job, rows); objects[job.object_path] = contents
    return upload_complete(job, {"file_size": len(contents), "file_checksum": hashlib.sha256(contents).hexdigest()})


def test_prepare_idempotency_slots_and_file_replacement(manager_user, fake_storage):
    objects, signed = fake_storage
    job, data = make_job(manager_user)
    assert prepare(data, manager_user.id)["id"] == job.id
    assert len(signed) == 2
    with pytest.raises(HTTPException) as error:
        make_job(manager_user)
    assert error.value.code == 409
    db.session.rollback()
    manual, _ = make_job(manager_user, "manual")
    assert manual.id != job.id
    objects[job.object_path] = b"old"
    job.status = "completed"; db.session.commit()
    new, _ = make_job(manager_user)
    assert job.object_path not in objects
    assert new.object_path != job.object_path


def test_callback_is_fast_idempotent_and_worker_imports(manager_user, fake_storage):
    objects, _ = fake_storage
    user = _get_or_create_user("charishma.sonani@example.com", "Charishma", "Sonani", "TEST-CHARISHMA")
    job, _ = make_job(manager_user)
    result = submit(job, [_sample_row(mbi="synthetic-identifier")], objects)
    assert result["status"] == "queued"
    assert KaironChartRecord.query.count() == 0
    again = upload_complete(job, {"file_size": job.file_size, "file_checksum": job.file_checksum})
    assert again["status"] == "queued"
    process_job(job)
    assert job.status == "completed", job.error
    assert KaironChartRecord.query.one().user_id == user.id
    assert KaironChartHistory.query.count() == 1
    assert KaironImportChunk.query.count() == 1
    assert b"synthetic-identifier" not in objects[job.object_path]


def test_missing_and_wrong_size_files_are_not_queued(manager_user, fake_storage):
    objects, _ = fake_storage; job, _ = make_job(manager_user)
    with pytest.raises(HTTPException) as error:
        upload_complete(job, {"file_size": 1, "file_checksum": "b" * 64})
    assert error.value.code == 409
    db.session.rollback(); objects[job.object_path] = b"abc"
    with pytest.raises(HTTPException) as error:
        upload_complete(job, {"file_size": 1, "file_checksum": "b" * 64})
    assert error.value.code == 422
    db.session.rollback(); assert job.status == "uploading"


def test_checksum_tampering_fails_before_chart_writes(manager_user, fake_storage):
    objects, _ = fake_storage; job, _ = make_job(manager_user)
    submit(job, [_sample_row(mbi="synthetic")], objects)
    objects[job.object_path] = objects[job.object_path].replace(b"A", b"B", 1)
    job.file_checksum = "f" * 64; db.session.commit()
    process_job(job)
    assert job.status == "failed"
    assert "checksum" in job.error
    assert KaironChartRecord.query.count() == 0


def test_worker_recovers_upload_without_browser_callback(manager_user, fake_storage):
    objects, _ = fake_storage; job, _ = make_job(manager_user)
    objects[job.object_path] = encrypt(job, [_sample_row(mbi="synthetic")])
    import_id = job.id
    run_once()
    assert db.session.get(StorageImport, import_id).status == "completed"


def test_worker_resumes_committed_chunk_without_duplicate_audit(manager_user, fake_storage, monkeypatch):
    import app.kairon.services as kairon
    objects, _ = fake_storage
    _get_or_create_user("charishma.sonani@example.com", "Charishma", "Sonani", "TEST-CHARISHMA")
    job, _ = make_job(manager_user, count=1001)
    rows = [_sample_row(mbi=f"synthetic-{index}") for index in range(1001)]
    submit(job, rows, objects)
    original = kairon.process_import_chunk
    def interrupted(*args, **kwargs):
        if args[1] == 1:
            raise RuntimeError("simulated worker interruption")
        return original(*args, **kwargs)
    monkeypatch.setattr(kairon, "process_import_chunk", interrupted)
    process_job(job)
    assert job.status == "failed"
    assert KaironChartRecord.query.count() == 1000
    retry(job)
    monkeypatch.setattr(kairon, "process_import_chunk", original)
    process_job(job)
    assert job.status == "completed", job.error
    assert KaironChartRecord.query.count() == 1001
    assert KaironChartHistory.query.count() == 1001
    assert KaironImportChunk.query.count() == 2


def test_all_blocks_validated_before_writing(manager_user, fake_storage):
    objects, _ = fake_storage; job, _ = make_job(manager_user, count=1001)
    rows = [_sample_row(mbi=f"synthetic-{index}") for index in range(1001)]
    rows[-1]["level"] = "invalid"
    submit(job, rows, objects); process_job(job)
    assert job.status == "failed"; assert "Row 1001" in job.error
    assert KaironChartRecord.query.count() == 0


def test_manual_duplicate_across_blocks_rejected(manager_user, fake_storage):
    objects, _ = fake_storage; job, _ = make_job(manager_user, "manual", 1001)
    row = {"userId": manager_user.id, "date": "2026-10-03", "productionCount": 1,
           "techIssuesDowntimeHours": 0, "noInventoryIdleTimeHours": 0, "leaveHours": 0, "meetingEngagementHours": 0}
    submit(job, [row] * 1001, objects); process_job(job)
    assert job.status == "failed"; assert "duplicate user/date" in job.error


def test_api_requires_manager_and_enforces_ownership(api_client, manager_user, employee_user, fake_storage):
    api_client.login("test-employee@example.com", "test-password")
    status, _ = api_client.post("/api/file-imports", {"kind": "kairon", "sourceFilename": "test.csv", "sourceChecksum": "a" * 64, "totalRows": 1, "requestId": str(uuid.uuid4())})
    assert status == 403
    _login_manager(api_client, manager_user)
    status, body = api_client.post("/api/file-imports", {"kind": "kairon", "sourceFilename": "test.csv", "sourceChecksum": "a" * 64, "totalRows": 1, "requestId": str(uuid.uuid4())})
    assert status == 201, body
    job = db.session.get(StorageImport, body["data"]["id"])
    job.uploaded_by_id = employee_user.id; db.session.commit()
    status, _ = api_client.get(f"/api/file-imports/{job.id}")
    assert status == 403


def test_advisory_lock_prevents_two_workers_for_same_kind(app):
    with kind_lock("kairon") as acquired:
        assert acquired
        with kind_lock("kairon") as second:
            assert not second
        with kind_lock("manual") as separate:
            assert separate


def test_manual_worker_imports_and_preserves_review_status(manager_user, lead_user, employee_user, fake_storage):
    from app.manual_daily_records.models import ManualDailyRecord
    objects, _ = fake_storage; job, _ = make_job(manager_user, "manual")
    row = {"userId": employee_user.id, "date": "2026-10-03", "productionCount": 4,
           "techIssuesDowntimeHours": 0, "noInventoryIdleTimeHours": 0, "leaveHours": 0, "meetingEngagementHours": 0}
    submit(job, [row], objects); process_job(job)
    assert job.status == "completed", job.error
    record = ManualDailyRecord.query.one()
    assert record.production_count == 4
    assert record.status == "pending"


def test_kairon_50000_rows(manager_user, fake_storage):
    objects, _ = fake_storage
    _get_or_create_user("charishma.sonani@example.com", "Charishma", "Sonani", "TEST-CHARISHMA")
    job, _ = make_job(manager_user, count=50_000)
    rows = [_sample_row(mbi=f"synthetic-large-{index}") for index in range(50_000)]
    submit(job, rows, objects); process_job(job)
    assert job.status == "completed", job.error
    assert KaironChartRecord.query.count() == 50_000
    assert KaironChartHistory.query.count() == 50_000
    assert KaironImportChunk.query.count() == 50


def test_new_tables_have_rls_enabled_and_slots_are_seeded():
    from sqlalchemy import text
    rows = db.session.execute(text("SELECT relname, relrowsecurity FROM pg_class WHERE relname IN ('storage_imports', 'storage_import_slots')")).all()
    assert len(rows) == 2
    assert all(enabled for _, enabled in rows)
    assert {slot.kind for slot in StorageImportSlot.query.all()} == {"kairon", "manual"}


def test_replaced_object_recreated_by_old_token_is_cleaned(manager_user, fake_storage):
    from app.storage_imports.services import cleanup_replaced_files
    objects, _ = fake_storage; old, _ = make_job(manager_user)
    old_id, path = old.id, old.object_path
    old.status = "completed"; db.session.commit()
    current, _ = make_job(manager_user)
    objects[path] = b"late old upload"
    current_path = current.object_path
    objects[current_path] = b"current"
    cleanup_replaced_files()
    assert path not in objects
    assert objects[current_path] == b"current"
    assert db.session.get(StorageImport, old_id).status == "completed"


def test_concurrent_prepare_only_reserves_one_slot(app, manager_user, fake_storage):
    from concurrent.futures import ThreadPoolExecutor
    user_id = manager_user.id
    def create():
        with app.app_context():
            try:
                data = {"kind": "kairon", "source_filename": "test.csv", "source_checksum": "a" * 64,
                        "total_rows": 1, "request_id": uuid.uuid4()}
                prepare(data, user_id)
                return 201
            except HTTPException as exc:
                db.session.rollback()
                return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(), range(2)))
    assert sorted(results) == [201, 409]
    assert StorageImport.query.count() == 1
