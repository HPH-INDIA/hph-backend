# Signed-storage background imports

The frontend authorizes a private Supabase upload using `POST /api/file-imports`, transfers an encrypted file directly to Storage with TUS, and calls `POST /api/file-imports/{id}/upload-complete`. The callback verifies object presence and size, saves the expected checksum, and returns 202. An embedded or separate worker verifies the checksum and authenticated contents before changing records. HTTP requests never process chart or manual rows.

## Deployment

Deploy the backend migration and configure import processing before the new frontend. Legacy synchronous APIs remain compatible; the frontend no longer calls them.

Set on both the backend and worker:

- `SUPABASE_URL`: project HTTPS URL.
- `SUPABASE_STORAGE_SECRET_KEY`: backend secret/service-role key. Never expose it through frontend environment variables.
- `SUPABASE_IMPORT_BUCKET=hph-imports`
- `IMPORT_MAX_FILE_BYTES=50000000`: Free-plan per-file cap, including encryption/base64 overhead.
- Existing `DATABASE_URL`, `SECRET_KEY`, `ENCRYPTION_MASTER_KEY`, and `KAIRON_IDENTITY_KEY`. Both processes must use the same database and encryption/identity keys. Use a direct connection or session-mode pooler, **not transaction pooling**: worker exclusion uses PostgreSQL session advisory locks.

With backend configuration loaded, run:

```sh
python -m flask --app run:app db upgrade
python -m flask --app run:app setup-import-storage
```

The setup command creates/verifies a private bucket restricted to `application/octet-stream`. Do not add public or anonymous object-write policies. Upload authorization uses object-specific signed tokens with overwriting disabled.

Start the separate long-lived worker:

```sh
python -m flask --app run:app process-file-imports
```

Use `--once` for an operational one-pass check. For a dedicated worker, disable `IMPORT_EMBEDDED_PROCESSING` on the web service. Alternatively use an existing always-on server. The queue is PostgreSQL-backed and independent of Redis and `CELERY_TASK_ALWAYS_EAGER`.

## Lifecycle and recovery

There is one server-locked slot per type across managers. Kairon and manual can each have one current file. A second import of the same type is rejected while the first is uploading, queued, processing, or failed awaiting retry. Failed imports can be retried or dismissed to abandon their slot. Incomplete transfers can be cancelled.

Files use unique immutable paths. Before authorizing a replacement, the backend deletes the previous terminal import's file. PostgreSQL retains job metadata/results, not source-file archives. A maintenance sweep removes superseded objects, including any recreated by a late old upload session. Signed tokens/TUS sessions can outlive abandonment: an obsolete object may exist briefly until cleanup, but can never replace or process the current import's file.

The browser retains its encrypted file and TUS session URL in memory for byte-offset resumption. Browser closure before transfer completion requires cancelling the old slot and selecting the file again; raw rows are never persisted in localStorage. After queueing, job state survives browser closure and progress is restored on manager login. Continuous processing after browser closure requires a dedicated always-on worker or a web service that remains running. The worker discovers completed objects even if the completion callback was lost. Unfinished uploads expire three hours after their last authorization.

The worker validates all blocks before writes, checks current uploader permissions, and enforces manual team scope, duplicate user/date rejection, and last-working-day limits. Records, audit history, and progress commit together in bounded 1,000-row transactions. A crashed worker resumes from committed progress; a session advisory lock prevents concurrent workers for the same type. Handled failures persist for explicit retry or abandonment.

Committed rows become visible incrementally. Later failures do not undo earlier chunks; retry continues from the checkpoint. The import is not a whole-file atomic transaction. Existing Kairon identity/upsert behavior is preserved. Manual comparisons now run on the worker and unchanged rows preserve review state.

## Encryption

The browser creates a `.hph-import` file from parser-allowlisted rows rather than uploading the original workbook. The original filename is retained as job metadata. Existing parsers remove patient-name columns.

Each line is base64 of `12-byte nonce || AES-256-GCM ciphertext+tag`, containing at most 1,000 rows. Associated data is `hph-import:v1:{import-id}:{block-index}`. Only the last block may be short. A random per-import key is delivered through the existing authenticated/encrypted API and persisted wrapped with `ENCRYPTION_MASTER_KEY`. It is not a Supabase credential, and normal API-key rotation does not prevent resumption. Raw MBI is encrypted in Storage and becomes keyed fingerprints in the record database. Neither patient names nor plaintext MBI are persisted in Storage or the record database. Keep decrypted payload logging disabled in production.

## Verification

`tests/test_storage_imports.py` uses real local PostgreSQL, mocking only Storage network operations: 50,000 records, manual processing, permission/ownership checks, upload slots, callbacks, corruption, all-block validation, recovery, and worker exclusion. Existing Kairon/manual tests are also run.

Frontend tests verify orchestration, response loss, retained ciphertext, logout cancellation, login recovery, authenticated encryption, and TUS resumption. A live Supabase smoke upload is still required after credentials and bucket configuration; local tests cannot verify provider credentials or CORS.

## Render Free configuration

The blueprint now defines only the free web service, with `IMPORT_EMBEDDED_PROCESSING=true` and `WEB_CONCURRENCY=1`. For an existing dashboard-managed service, set these two environment variables manually and keep Docker Command `sh scripts/start-web.sh`. No separate Render worker or one-off job is required. There is no new migration for this mode.

Authenticated upload completion, retry, and progress/list requests start a daemon thread if needed. This thread has its own Flask application context and database session, runs one queue pass, and exits. HTTP requests return without waiting for chart processing. A PostgreSQL advisory lock serializes both import types across web processes; existing per-type locks still protect against dedicated workers. File validation uses two passes over encrypted bytes, retaining one decrypted block at a time, rather than all parsed rows.

Keep the app tab open until processing finishes. Render Free can sleep after 15 minutes without inbound traffic and restart at any time. Legitimate progress polling lets the user track the active import; this is not a guarantee of uptime. When the service wakes, a manager's next progress request starts another pass and resumes committed chunks. Recovered failed jobs require Retry. File transfers interrupted before submission still require selecting the file again.

CPU and the 512 MB memory limit are shared with Flask. Validate a representative 50,000-row file and normal API traffic in test before relying on this mode. Two-pass validation reduces row memory but still holds the encrypted file in memory, and completion calculations use existing application routines. No guarantee is made that every file within the storage size limit fits the web service's memory.

## Completed-production counting

Completed-production reports use the Daily Refresh HTML key: MBI fingerprint + level + completion date. Task creation date, practice, program and coder do not split a completion. A shared query selects the lowest-ID eligible completed record per key before applying report filters, keeping totals and detail lists consistent across dashboard, efficiency, monthly goals and team reports. Superseded batches are excluded. Legacy rows without an MBI fingerprint remain individually counted because their beneficiary identity cannot be reconstructed safely.

This is reporting deduplication: chart identity/upsert rules and source/audit records remain unchanged. Stage-target calculations are independent and unchanged. No schema migration or re-upload is required to apply this report change.
