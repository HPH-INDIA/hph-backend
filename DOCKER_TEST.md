# Backend Docker test environment

`Dockerfile.test` builds the current local API code. `compose.test.yaml` connects it to the hosted Supabase PostgreSQL database and Storage configured in the private `.env.test` file. It does not start Postgres or Redis.

For the full frontend/API stack, follow [the frontend test setup](../hph/DOCKER_TEST.md) and run Compose from `../hph`. Both test Compose files use `hph-test`; run only one of them at a time.

## Backend only

Use Docker Compose 2.30 or newer. Copy `.env.test.example` to `.env.test`, set permissions to `600`, then fill in the chosen Supabase project's connection, Storage secret, and application keys. Values in this raw environment file must be unquoted. Do not commit the private file.

Use a separate Supabase test project for isolated data. Copy the exact Session pooler URL from **Connect**, use `postgresql+psycopg2://`, percent-encode the password, and retain `sslmode=require`. [Supabase connection guide](https://supabase.com/docs/guides/database/connecting-to-postgres).

Generate application keys once for a new database using the command in the frontend setup. When reusing an existing database, preserve its existing encryption and identity keys. A different container name does not isolate database records.

```sh
docker compose -f compose.test.yaml config --quiet
docker compose -f compose.test.yaml build
# Explicit setup for the configured test project:
docker compose -f compose.test.yaml run --rm backend python -m flask --app run:app db upgrade
docker compose -f compose.test.yaml run --rm backend python -m flask --app run:app setup-import-storage
docker compose -f compose.test.yaml up -d --wait
docker compose -f compose.test.yaml exec backend python scripts/check-test-environment.py
```

The API listens at http://localhost:8083. To use the existing Vite UI on port 5173:

```sh
TEST_FRONTEND_ORIGIN=http://localhost:5173 docker compose -f compose.test.yaml up -d --wait
```

Point Vite's `API_PROXY_TARGET` at `http://localhost:8083` and use the same hostname in your browser. `TEST_BACKEND_PORT` changes the API's local port; `TEST_SECURE_COOKIES=true` enables secure cookies for HTTPS.

Outgoing mail is suppressed and payload logging is disabled in the test image. Celery uses eager tasks with in-memory transport; imports run inside the web process. Container startup does not migrate/reset the database or configure Storage. The verification script issues only reads and does not print credentials.

```sh
docker compose -f compose.test.yaml logs -f backend
docker compose -f compose.test.yaml down
```

Stopping this stack preserves all Supabase data.
