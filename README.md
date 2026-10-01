# Vitalyse Health — Backend

Flask-based REST API backend for Vitalyse Health.

## Tech stack

- **Flask 3** — application/web framework
- **Flask-SQLAlchemy** + **Flask-Migrate (Alembic)** — ORM and database migrations
- **flask-smorest** / **marshmallow** / **webargs** — REST API layer, request/response schemas
- **PostgreSQL** (`psycopg2-binary`) — primary database
- **Celery** + **Redis** — background/async task processing
- **Flask-Mail** — outbound email
- **argon2-cffi** — password hashing
- **cryptography** — field-level encryption utilities

## Project structure

```
backend/
├── app/
│   ├── __init__.py       # app factory, extension init, blueprint registration
│   ├── config.py         # Config, reads DATABASE_URL / SECRET_KEY from env
│   ├── extensions.py     # shared SQLAlchemy / Migrate instances
│   ├── roles/            # RoleType model + blueprint (role hierarchy, session timeouts)
│   ├── features/         # blueprint (stub)
│   ├── users/            # blueprint (stub)
│   ├── sessions/         # blueprint (stub)
│   ├── mailing/          # email sending (stub)
│   ├── encryption/       # encryption helpers (stub)
│   └── cohorts/          # coder cohorts, stage progression, target configuration
├── migrations/           # Alembic migration environment + versioned migrations
├── run.py                # local entrypoint (`python run.py`)
└── requirements.txt
```

All blueprints are mounted under the `/api` prefix (see `app/__init__.py`).

## Setup

1. Create and activate a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Create a `.env` file in `backend/` (see `.env.example`) with at least:
   ```
   DATABASE_URL=postgresql://user:password@localhost:5432/vitalyse_health
   SECRET_KEY=<a-random-secret>
   ```
4. Apply database migrations:
   ```bash
   export FLASK_APP=run.py
   flask db upgrade
   ```
5. Run the app:
   ```bash
   python run.py
   ```

## Deploy to Render

The repository includes a `render.yaml` Blueprint for a Docker-based web
service in Render's Singapore region. Render supplies `PORT`; Gunicorn already
binds to it, and Render checks `/healthz` before routing traffic to a deploy.
The Blueprint starts on Render's free plan to avoid creating a charge; select a
paid always-on plan before treating Render as the production cutover target.

1. Create a Render Blueprint from this repository.
2. Supply the four values marked `sync: false` from the production secret
   store. Use the Supabase **session pooler** URL on port `5432` with
   `sslmode=require`; Render's outbound network is IPv4-only.
3. Keep the existing Cloud Run service available until the Render health and
   authenticated API checks pass and the frontend proxy is switched.

For local verification, copy `.env.render.example` to `.env.render` and fill in
the real values. If you have access to the `hph-inhouse` GCP project, run
`./scripts/export_render_env.sh` to copy the existing production values without
printing them. `.env.render` is ignored by Git and must never be committed.

## Email delivery

Mail is rendered by the API and queued in Redis; a separate Celery worker
performs SMTP delivery. Local development uses Mailpit on `localhost:1025`.
For testing or production, configure an authenticated SMTP relay through the
variables in `.env.example`, set `EMAIL_VALIDATE_CONFIG=true`, and run both
processes:

```bash
python run.py
celery -A celery_worker.celery worker --loglevel=INFO
```

For STARTTLS (normally port 587), set `EMAIL_USE_TLS=true` and
`EMAIL_USE_SSL=false`; for implicit TLS (normally port 465), reverse those
values. Never enable both. `EMAIL_VALIDATE_CONFIG=true` makes the API and
worker fail fast on incomplete credentials, an invalid sender, a local-only
SMTP host, or a non-HTTPS frontend URL.

After deploying the API, worker, Redis, and database migrations, enqueue an
end-to-end delivery check from the deployed environment:

```bash
flask --app run.py mail-smoke-test you@example.com
```

Confirm the message arrives and that `email_logs` contains a `sent` row with
template name `deployment_smoke_test`. SMTP credentials must be stored in the
deployment platform's secret manager, not committed to this repository.

## Database migrations

Managed via Flask-Migrate/Alembic in [`migrations/`](migrations). Common commands (with `FLASK_APP=run.py` exported):

```bash
flask db migrate -m "describe change"   # autogenerate a new revision from model changes
flask db upgrade                        # apply pending migrations
flask db downgrade                      # roll back one revision
```

Model changes must be imported in `app/__init__.py` before running `flask db migrate` so autogenerate can detect them.

## Current status

- `roles`: `RoleType` model exists and is seeded with five fixed roles (`super_admin`, `admin`, `manager`, `lead`, `employee`); the model enforces that seed rows can't be edited or deleted.
- `mailing`: asynchronous SMTP delivery for temporary-password and password-change messages, with retry/backoff and metadata-only audit logs. Local Mailpit and authenticated testing/production SMTP are selected entirely through environment configuration.
- `cohorts`: Training->M1->M2->M3->M4->Steady State coder progression, cohort assignment, and date-versioned stage targets. Read endpoints are open to any authenticated user; writes require `super_admin`. One-time backfill from the legacy Daily_Refresh spreadsheet pipeline: `flask import-legacy-data --input /path/to/Daily_Refresh/input`. Tests: `pytest` (spins up/migrates a separate `vitalyse_health_test` database - see `tests/conftest.py`; override with `TEST_DATABASE_URL`).
- `kairon`: bulk upload of Kairon's per-chart coding/QA ledger (Program, Level, Status, Coding Analyst, Actions, Last Action, Created, Completed, TAT, Age, Practice) as one snapshot batch per manager-chosen as-of date; Patient name and MBI are never modelled and any payload mentioning either column is rejected outright. Coding-analyst names resolve against the platform's login identity (`User`, matched by first/last name) - not the separate `Coder` entity `cohorts` maintains for stage/ramp tracking; unresolved names go to a manager-only review queue (`/api/kairon/analyst-reviews`) rather than auto-creating an account. A same-date re-upload supersedes the prior batch for that date (kept for audit) rather than merging row-by-row. Read endpoints (`GET /api/kairon/upload-template`, `/uploads`, `/charts`) are open to any authenticated user and support filtering by a group of users via a repeated `userIds` param (an ad hoc list - true cohort-based filtering needs a `Coder`-to-`User` bridge that doesn't exist yet); uploading and resolving a review require the `kairon_management` feature, granted to the Manager role by default.
- `manual_daily_records`: a logged-in user can post their own production counts, optional meeting type, and four capped (0-10h) hour categories for one calendar day via `POST /api/manual-daily-records`. Managers can also import team records from the controlled `.xlsx` template via `/api/manual-daily-records/bulk-upload`; uploads validate every row before making any changes. Records are unique per (user, date). Employee submissions and edits are `pending` until their direct reporting lead approves or rejects them; lead submissions and edits are automatically approved. Individual review endpoints and `/api/reports/manual/reviews/bulk-approve|bulk-reject` require a lead with Reports write access and are scoped to that lead's direct employees. Managers have read-only review oversight. `GET /api/manual-daily-records` supports date, user, group, exclusion, and status filters within the viewer's scope. `GET /api/reports/manual/team-day?date=YYYY-MM-DD` returns each lead separately from their coders, including missing daily entries, for leads and managers with Reports read access.
