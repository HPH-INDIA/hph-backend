# HPH backend (hph-backend)

Flask 3 REST API (flask-smorest/marshmallow, SQLAlchemy + Flask-Migrate/Alembic, PostgreSQL, Celery + Redis). The React frontend lives in the separate `hph` repo.

## Team rules (apply to every task)

- **Cross-repo work:** HPH is two repos — `hph` (React frontend) and `hph-backend` (Flask API). For any task touching both, or touching an API call/route, inspect both repos and explain the API flow (frontend `src/api/*` → `/api/...` → backend `app/<module>/routes.py` → schema/service) before editing. If the other repo is not checked out, say so.
- **API compatibility:** preserve the frontend's `/api` calls and the backend's `/api` endpoints (paths, methods, params, `{status, message, data}` envelope, auth/CSRF/encryption behavior) unless the task explicitly asks for a breaking change.
- **Scope:** keep changes limited to the task. No drive-by refactors, dependency bumps, or formatting churn.
- **Data:** use synthetic test data only. Never use production data or real exports.
- **Secrets:** never read, print, or expose `.env`, `.env.*` (the `*.example` templates are fine), keys, or credential files. Never upload secrets.
- **Never:** deploy, run deployment scripts/workflows, push to `main` (pushing `main` triggers the production deploy workflow), or run migrations against a non-disposable database.
- **Reporting:** state the exact checks you ran and their results. Mark skipped or failed checks honestly; never imply something passed that you did not run.

## Subagents (`.claude/agents/`, shared by both repos)

- `hph-api-contract-reviewer` — read-only frontend↔backend contract comparison.
- `hph-frontend-test-runner` — lint/typecheck/build/auth tests for `hph`; no source edits.
- `hph-backend-test-runner` — pytest for `hph-backend`, only with a disposable test database; no source edits.

## Repo facts

- App factory and blueprint registration: `app/__init__.py`. Most blueprints declare `url_prefix="/api"`; Entra sign-in uses `/auth`. Routes in `app/<module>/routes.py`, schemas in `app/<module>/schemas.py`, auth helpers in `app/auth.py`, sessions/CSRF in `app/sessions/`, payload encryption hooks in `app/encryption/`.
- `app/config.py` calls `load_dotenv()` and requires `SECRET_KEY`; encryption requires `ENCRYPTION_MASTER_KEY`. Supply synthetic values via the shell for tests — do not open `.env`.
- Migrations live in `migrations/`. Do not create or apply migrations unless the task asks; never apply them to a shared, development, or production database.
- Do not run deploy/export scripts (`scripts/export_render_env.sh`, Render/Cloud Run/production workflows) or `compose.test.yaml` (it targets a real Supabase project).

## Tests

```bash
pip install -r requirements.txt
pytest
```

`tests/conftest.py` sets `DATABASE_URL` from `TEST_DATABASE_URL` (default `postgresql+psycopg2://localhost:5432/vitalyse_health_test`), creates that database if missing, runs the full Alembic upgrade, and deletes rows from many tables after every test. Only run `pytest` with `TEST_DATABASE_URL` explicitly pointing at a disposable local test database; otherwise report the tests as skipped. Re-read `tests/conftest.py` before running in case this behavior changed.
