import datetime as dt

from app.encryption.passwords import hash_password
from app.extensions import db
from app.kairon.models import KaironChartRecord, KaironUploadBatch
from app.kairon.services import (
    build_upload_template_csv,
    complete_cumulative_import,
    import_batch,
    normalize_analyst_name,
    process_import_chunk,
    resolve_user,
    start_cumulative_import,
)
from app.roles.models import Role, RoleType
from app.users.models import User


def _login_superadmin(api_client):
    status, body = api_client.login("superadmin", "superadmin")
    assert status == 200, body
    return body


def _login_manager(api_client, manager_user):
    status, body = api_client.login("test-manager@example.com", "test-password")
    assert status == 200, body
    return body


def _get_or_create_user(email, first_name, last_name, emp_id):
    """Get-or-create by a fixed test email, same rationale as
    conftest.manager_user/employee_user - the `users` table isn't in the
    per-test cleanup list, so identity-resolution tests reuse a stable
    identity across runs instead of accumulating (or colliding on) rows."""
    role_type = RoleType.query.filter_by(code="employee").one()
    role = Role.query.filter_by(role_type_id=role_type.id).first()
    user = User.query.filter_by(email=email).first()
    if user is None:
        user = User(
            email=email,
            first_name=first_name,
            last_name=last_name,
            emp_id=emp_id,
            role_id=role.id,
            password_hash=hash_password("test-password"),
            first_login=False,
            is_active=True,
        )
        db.session.add(user)
        db.session.commit()
    return user


def _sample_row(**overrides):
    row = {
        "program": "PVP",
        "level": "1LR",
        "status": "Completed",
        "codingAnalyst": "Charishma Sonani - HPH Coding Analyst",
        "actions": 1,
        "lastAction": "(Offshore) No Findings (20% to 2LR, 80% close out)",
        "created": "2026-08-28",
        "completed": "2026-09-03",
        "tat": 6,
        "age": 6,
        "practice": "Primary Care Health Partners - Vermont Llp",
    }
    row.update(overrides)
    return row


# --------------------------------------------------------------------------
# Service-level: name resolution and the template, without the HTTP/crypto
# layer - mirrors test_services.py's style for cohorts.
# --------------------------------------------------------------------------


def test_normalize_analyst_name_strips_role_suffix():
    assert normalize_analyst_name("Charishma Sonani - HPH Coding Analyst") == "Charishma Sonani"
    assert normalize_analyst_name("  Mayur Charde  ") == "Mayur Charde"
    assert normalize_analyst_name(None) == ""


def test_resolve_user_matches_by_name_case_insensitively():
    user = _get_or_create_user("charishma.sonani@example.com", "Charishma", "Sonani", "TEST-CHARISHMA")

    resolved = resolve_user("charishma sonani - hph coding analyst")
    assert resolved is not None
    assert resolved.id == user.id


def test_resolve_user_returns_none_when_nothing_matches():
    assert resolve_user("Nobody Real - HPH Coding Analyst") is None


def test_upload_template_offers_mbi_but_never_patient():
    csv_text = build_upload_template_csv()
    header = csv_text.strip().splitlines()[0]
    columns = header.split(",")
    assert columns == [
        "MBI", "Program", "Level", "Status", "Coding Analyst", "Actions",
        "Last Action", "Created", "Completed", "TAT", "Age", "Practice",
    ]
    assert "patient" not in header.lower()
    assert "MBI" in columns


def test_cumulative_import_is_idempotent_and_updates_status():
    known = _get_or_create_user("charishma.sonani@example.com", "Charishma", "Sonani", "TEST-CHARISHMA")
    row = {
        "mbi": "4VD5-P17-QX99",
        "program": "PVP",
        "level": "1LR",
        "status": "Active",
        "coding_analyst": "Charishma Sonani - HPH Coding Analyst",
        "actions": 1,
        "last_action": "Assigned",
        "created_date": dt.date(2026, 9, 1),
        "completed_date": None,
        "tat_days": None,
        "age_days": 1,
        "practice": "Some Practice",
    }

    first = start_cumulative_import("first.csv", "a" * 64, 1, _first_superadmin_id())
    first, _ = process_import_chunk(first.id, 0, "b" * 64, [row])
    complete_cumulative_import(first.id)
    assert first.inserted_count == 1
    assert KaironChartRecord.query.count() == 1

    second = start_cumulative_import("same-again.csv", "c" * 64, 1, _first_superadmin_id())
    second, _ = process_import_chunk(second.id, 0, "d" * 64, [row])
    complete_cumulative_import(second.id)
    assert second.unchanged_count == 1
    assert KaironChartRecord.query.count() == 1

    row["status"] = "Completed"
    row["completed_date"] = dt.date(2026, 9, 3)
    third = start_cumulative_import("changed.csv", "e" * 64, 1, _first_superadmin_id())
    third, _ = process_import_chunk(third.id, 0, "f" * 64, [row])
    complete_cumulative_import(third.id)
    assert third.updated_count == 1
    record = KaironChartRecord.query.one()
    assert record.status == "Completed"
    assert record.user_id == known.id


def test_import_batch_matches_known_user_and_skips_unmatched():
    known = _get_or_create_user("charishma.sonani@example.com", "Charishma", "Sonani", "TEST-CHARISHMA")

    rows = [
        {
            "program": "PVP", "level": "1LR", "status": "Completed",
            "coding_analyst": "Charishma Sonani - HPH Coding Analyst",
            "actions": 1, "last_action": "No findings, close out",
            "created_date": dt.date(2026, 8, 28), "completed_date": dt.date(2026, 9, 3),
            "tat_days": 6, "age_days": 6, "practice": "Some Practice",
        },
        {
            "program": "PVP", "level": "1LR", "status": "On Hold",
            "coding_analyst": "Totally Unknown Person - HPH Coding Analyst",
            "actions": 1, "last_action": "Place on Hold",
            "created_date": dt.date(2026, 8, 28), "completed_date": None,
            "tat_days": None, "age_days": 6, "practice": "Some Practice",
        },
    ]

    batch = import_batch(dt.date(2026, 9, 3), rows, uploaded_by_id=_first_superadmin_id())

    assert batch.row_count == 2
    assert batch.matched_count == 1
    assert batch.unmatched_count == 1
    assert batch.unmatched_names == ["Totally Unknown Person - HPH Coding Analyst"]

    records = KaironChartRecord.query.filter_by(batch_id=batch.id).all()
    assert len(records) == 1
    assert records[0].coding_analyst_raw.startswith("Charishma")
    assert records[0].user_id == known.id


def test_import_batch_supersedes_same_as_of_date():
    rows = [_service_row()]
    first = import_batch(dt.date(2026, 9, 3), rows, uploaded_by_id=_first_superadmin_id())
    second = import_batch(dt.date(2026, 9, 3), rows, uploaded_by_id=_first_superadmin_id())

    db.session.refresh(first)
    assert first.superseded_at is not None
    assert first.superseded_by_id == second.id
    assert second.superseded_at is None


def _service_row():
    return {
        "program": "PVP", "level": "1LR", "status": "Completed",
        "coding_analyst": "Anyone - HPH Coding Analyst",
        "actions": 1, "last_action": "No findings, close out",
        "created_date": dt.date(2026, 8, 28), "completed_date": dt.date(2026, 9, 3),
        "tat_days": 6, "age_days": 6, "practice": "Some Practice",
    }


def _first_superadmin_id():
    role_type = RoleType.query.filter_by(code="super_admin").one()
    role = Role.query.filter_by(role_type_id=role_type.id).first()
    return User.query.filter_by(role_id=role.id).first().id


# --------------------------------------------------------------------------
# API-level: role gating, the PHI guard, and the end-to-end upload flow -
# through the real encrypted request/response cycle (see conftest.ApiClient).
# --------------------------------------------------------------------------


def test_upload_rejects_non_manager(api_client, employee_user):
    status, _ = api_client.login("test-employee@example.com", "test-password")
    assert status == 200

    status, body = api_client.post(
        "/api/kairon/uploads", {"asOfDate": "2026-09-03", "rows": [_sample_row()]}
    )
    assert status == 403, body


def test_upload_rejects_patient_or_mbi_columns(api_client, manager_user):
    _login_manager(api_client, manager_user)

    bad_row = _sample_row(Patient="CABRERA JR, OSCAR", MBI="4VD5P17QX99")
    status, body = api_client.post("/api/kairon/uploads", {"asOfDate": "2026-09-03", "rows": [bad_row]})
    assert status == 400, body
    assert "patient" in body["message"].lower() or "mbi" in body["message"].lower()

    # confirmed nothing was written
    assert KaironUploadBatch.query.count() == 0


def test_upload_rejects_rows_missing_template_columns(api_client, manager_user):
    _login_manager(api_client, manager_user)

    incomplete_row = _sample_row()
    incomplete_row.pop("tat")
    status, body = api_client.post(
        "/api/kairon/uploads", {"asOfDate": "2026-09-03", "rows": [incomplete_row]}
    )

    assert status == 422, body
    assert KaironUploadBatch.query.count() == 0


def test_manager_can_upload_and_list_charts(api_client, manager_user):
    _login_manager(api_client, manager_user)

    _get_or_create_user("charishma.sonani@example.com", "Charishma", "Sonani", "TEST-CHARISHMA")

    status, body = api_client.post(
        "/api/kairon/uploads",
        {
            "asOfDate": "2026-09-03",
            "sourceFilename": "coding_ops_tasks_3rd_Sept.csv",
            "rows": [_sample_row(), _sample_row(codingAnalyst="Someone New - HPH Coding Analyst")],
        },
    )
    assert status == 201, body
    assert body["data"]["rowCount"] == 2
    assert body["data"]["matchedCount"] == 1
    assert body["data"]["unmatchedCount"] == 1
    assert body["data"]["unmatchedNames"] == ["Someone New - HPH Coding Analyst"]

    status, body = api_client.get("/api/kairon/charts")
    assert status == 200, body
    assert len(body["data"]) == 1
    assert body["data"][0]["codingAnalyst"] == "Charishma Sonani - HPH Coding Analyst"
    assert all("patient" not in str(row).lower() for row in body["data"])


def test_manager_can_upload_cumulative_chunks(api_client, manager_user):
    _login_manager(api_client, manager_user)
    _get_or_create_user("charishma.sonani@example.com", "Charishma", "Sonani", "TEST-CHARISHMA")

    status, body = api_client.post(
        "/api/kairon/imports",
        {"sourceFilename": "mtd.csv", "fileChecksum": "a" * 64, "totalRows": 1},
    )
    assert status == 201, body
    import_id = body["data"]["id"]

    status, body = api_client.post(
        f"/api/kairon/imports/{import_id}/chunks/0",
        {"checksum": "b" * 64, "rows": [_sample_row(mbi="4VD5P17QX99")]},
    )
    assert status == 200, body
    assert body["data"]["processedCount"] == 1
    assert body["data"]["insertedCount"] == 1

    status, body = api_client.post(f"/api/kairon/imports/{import_id}/complete", None)
    assert status == 200, body
    assert body["data"]["status"] == "completed"


def test_cumulative_chunk_updates_new_duplicate_identity_and_preserves_history(api_client, manager_user):
    from app.kairon.models import KaironChartHistory, KaironImportChunk

    _login_manager(api_client, manager_user)
    _get_or_create_user("charishma.sonani@example.com", "Charishma", "Sonani", "TEST-CHARISHMA")
    status, body = api_client.post(
        "/api/kairon/imports",
        {"sourceFilename": "duplicate.csv", "fileChecksum": "a" * 64, "totalRows": 2},
    )
    assert status == 201, body
    import_id = body["data"]["id"]
    first = _sample_row(mbi="SYNTHETIC-DUPLICATE", status="Active", completed=None)
    changed = _sample_row(mbi="SYNTHETIC-DUPLICATE", status="Completed")
    payload = {"checksum": "b" * 64, "rows": [first, changed]}

    status, body = api_client.post(f"/api/kairon/imports/{import_id}/chunks/0", payload)
    assert status == 200, body
    progress = body["data"]
    assert progress["processedCount"] == 2
    assert progress["insertedCount"] == 1
    assert progress["updatedCount"] == 1
    record = KaironChartRecord.query.one()
    assert record.status == "Completed"
    history = KaironChartHistory.query.filter_by(chart_record_id=record.id).all()
    assert {(item.previous_status, item.status) for item in history} == {
        (None, "Active"),
        ("Active", "Completed"),
    }

    status, body = api_client.post(f"/api/kairon/imports/{import_id}/chunks/0", payload)
    assert status == 200, body
    assert body["data"] == progress
    assert KaironChartRecord.query.count() == 1
    assert KaironChartHistory.query.count() == 2
    assert KaironImportChunk.query.filter_by(batch_id=import_id).count() == 1
