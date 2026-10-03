# Signed-storage background imports

The frontend authorizes a private Supabase upload using `POST /api/file-imports`, transfers an encrypted file directly to Storage with TUS, and calls `POST /api/file-imports/{id}/upload-complete`. The callback verifies object presence and size, saves the expected checksum, and returns 202. A separate worker verifies the checksum and authenticated contents before changing records. HTTP requests never process chart or manual rows.

## Deployment

Deploy the backend migration and worker before the new frontend. Legacy synchronous APIs remain compatible; the frontend no longer calls them.

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

Use `--once` for an operational one-pass check. `render.yaml` includes `hph-import-worker` on a paid worker plan; editing the blueprint does not provision it. Alternatively use an existing always-on server. The queue is PostgreSQL-backed and independent of Redis and `CELERY_TASK_ALWAYS_EAGER`.

## Lifecycle and recovery

There is one server-locked slot per type across managers. Kairon and manual can each have one current file. A second import of the same type is rejected while the first is uploading, queued, processing, or failed awaiting retry. Failed imports can be retried or dismissed to abandon their slot. Incomplete transfers can be cancelled.

Files use unique immutable paths. Before authorizing a replacement, the backend deletes the previous terminal import's file. PostgreSQL retains job metadata/results, not source-file archives. A maintenance sweep removes superseded objects, including any recreated by a late old upload session. Signed tokens/TUS sessions can outlive abandonment: an obsolete object may exist briefly until cleanup, but can never replace or process the current import's file.

The browser retains its encrypted file and TUS session URL in memory for byte-offset resumption. Browser closure before transfer completion requires cancelling the old slot and selecting the file again; raw rows are never persisted in localStorage. After queueing, processing survives browser closure and progress is restored on manager login. The worker discovers completed objects even if the completion callback was lost. Unfinished uploads expire three hours after their last authorization.

The worker validates all blocks before writes, checks current uploader permissions, and enforces manual team scope, duplicate user/date rejection, and last-working-day limits. Records, audit history, and progress commit together in bounded 1,000-row transactions. A crashed worker resumes from committed progress; a session advisory lock prevents concurrent workers for the same type. Handled failures persist for explicit retry or abandonment.

Committed rows become visible incrementally. Later failures do not undo earlier chunks; retry continues from the checkpoint. The import is not a whole-file atomic transaction. Existing Kairon identity/upsert behavior is preserved. Manual comparisons now run on the worker and unchanged rows preserve review state.

## Encryption

The browser creates a `.hph-import` file from parser-allowlisted rows rather than uploading the original workbook. The original filename is retained as job metadata. Existing parsers remove patient-name columns.

Each line is base64 of `12-byte nonce || AES-256-GCM ciphertext+tag`, containing at most 1,000 rows. Associated data is `hph-import:v1:{import-id}:{block-index}`. Only the last block may be short. A random per-import key is delivered through the existing authenticated/encrypted API and persisted wrapped with `ENCRYPTION_MASTER_KEY`. It is not a Supabase credential, and normal API-key rotation does not prevent resumption. Raw MBI is encrypted in Storage and becomes keyed fingerprints in the record database. Neither patient names nor plaintext MBI are persisted in Storage or the record database. Keep decrypted payload logging disabled in production.

## Verification

`tests/test_storage_imports.py` uses real local PostgreSQL, mocking only Storage network operations: 50,000 records, manual processing, permission/ownership checks, upload slots, callbacks, corruption, all-block validation, recovery, and worker exclusion. Existing Kairon/manual tests are also run.

Frontend tests verify orchestration, response loss, retained ciphertext, logout cancellation, login recovery, authenticated encryption, and TUS resumption. A live Supabase smoke upload is still required after credentials and bucket configuration; local tests cannot verify provider credentials or CORS.
