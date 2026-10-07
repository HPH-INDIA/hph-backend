# HPH appraisal system

Appraisal cycles, configurable appraisal forms and Microsoft sign-in for HPH. Two microservices
(Appraisal, Form Builder) plus a plugin that the existing Flask app (the Access/BFF) loads.

- Requirements and design decisions: [docs/APPRAISAL_SERVICE_IMPLEMENTATION.md](docs/APPRAISAL_SERVICE_IMPLEMENTATION.md)
- Microsoft sign-in set-up, rollout and the request for IT: [docs/MICROSOFT_SIGN_IN.md](docs/MICROSOFT_SIGN_IN.md)

## Layout

```text
appraisal_system/
  README.md            this guide
  docs/                requirements, Microsoft sign-in
  ruff.toml            lint settings shared by everything below
  common/              shared library: errors, service tokens, HTTP + directory clients, Connexion wiring
  appraisal/           Appraisal microservice
  form_builder/        Form Builder microservice
  access_bff/          plugin for the existing Flask app: directory API, gateway, Microsoft sign-in
```

Each microservice is self-contained:

```text
appraisal/
  appraisal.yaml       OpenAPI spec, the source of truth; each operationId names its handler
  main.py              builds the Connexion app (gunicorn main:app)
  config.py, config_dbname.py     settings; DB_SCHEMA is the service's Postgres schema
  handlers/            thin: read the caller, call a service function, serialize
  services/            business rules and database work
  models/              SQLAlchemy models
  clients/             calls to other services
  migrations/          Alembic history kept inside the service's own schema
  tests/               conftest.py, factories.py, test_*.py
  Dockerfile, Dockerfile.dockerignore, gunicorn.conf.py, requirements.txt, test-requirements.txt
  common -> ../common  symlink, so the service imports the shared library as `common`
```

Outside this directory the change is small: one call in `app/__init__.py`
(`init_access_bff(app, api)`), three pinned libraries in `requirements.txt`, and two migrations
in `migrations/versions/` (appraisal feature seeds, Microsoft identity tables).

## How it fits together

```mermaid
flowchart LR
    Browser -->|session cookie, encrypted payloads| BFF[Flask app + access_bff]
    Browser -->|/auth/microsoft/*| BFF
    BFF <-->|OpenID Connect| Entra[Microsoft Entra ID]
    BFF -->|/api/appraisal/* + signed user token| AS[appraisal]
    BFF -->|/api/form-builder/* + signed user token| FB[form_builder]
    AS -->|resolve, validate + service token| FB
    AS -->|users, reporting lines + service token| DIR[access_bff directory API]
    FB -->|projects + service token| DIR
    AS --> ADB[(schema appraisal)]
    FB --> FDB[(schema forms)]
    BFF --> PDB[(schema public)]
```

1. People sign in to the Flask app, with Microsoft once `ENTRA_SIGN_IN_ENABLED` is on, and get
   the app's normal session cookie.
2. The gateway checks the user is still active, signs a 60-second Ed25519 token addressed to one
   service (user id, role type, project, feature grants) and forwards the request with
   `Authorization: Bearer <token>` and a `caller_id` header.
3. Each service verifies the token against the gateway's public key and checks `caller_id`
   matches. Handlers read the caller with `common.connexion_app.current_caller()`.
4. Services call each other and the directory with *service* tokens signed by their own key and
   `caller_id: -1`.

Every process holds its own private key and trusts others by public key, so a service can verify
user tokens but never mint one. gunicorn 22+ and Werkzeug 3 drop headers containing `_`, so the
services' `gunicorn.conf.py` sets `header_map = "dangerous"` (safe because `caller_id` is checked
against the token) and the directory relies on the token alone.

## Configuration

Generate a key pair per process with `python -m common.service_auth` (from a service directory).
Multi-line PEMs may be written with literal `\n`.

| Variable | Flask app (access_bff) | form_builder | appraisal |
| --- | --- | --- | --- |
| `SERVICE_AUTH_ISSUER` | `hph-bff` | `hph-form-builder` | `hph-appraisal` |
| `SERVICE_AUTH_PRIVATE_KEY` | own key | own key | own key |
| `SERVICE_AUTH_TRUSTED_ISSUERS` | `hph-appraisal`, `hph-form-builder` as `service` | `hph-bff` as `user`, `hph-appraisal` as `service` | `hph-bff` as `user` |
| `DATABASE_URL` | as today | same database, schema `forms` | same database, schema `appraisal` |
| `APPRAISAL_SERVICE_URL`, `FORM_BUILDER_SERVICE_URL` | private URLs of the services | | `FORM_BUILDER_SERVICE_URL` |
| `ACCESS_SERVICE_URL` | | private URL of the Flask app | private URL of the Flask app |
| `SERVICE_GATEWAY_TIMEOUT_SECONDS` | default `15` | | |
| `ENTRA_*`, `PASSWORD_LOGIN_ENABLED`, `FRONTEND_APP_URL` | see [docs/MICROSOFT_SIGN_IN.md](docs/MICROSOFT_SIGN_IN.md) | | |
| `SWAGGER_UI_ENABLED` | | `false` in production | `false` in production |
| `APPRAISAL_APP_URL` | | | frontend base for email links |
| `NOTIFICATIONS_ENABLED`, `SMTP_*` | | | mail delivery, off by default |
| `REMINDER_DAYS_BEFORE`, `NOTIFICATION_MAX_ATTEMPTS` | | | defaults `3`, `5` |

`SERVICE_AUTH_TRUSTED_ISSUERS` is JSON:
`{"hph-bff": {"publicKey": "-----BEGIN PUBLIC KEY-----\n...", "tokenTypes": ["user"]}}`.

## Running locally

The microservices use Flask 2.2 / Connexion 2 and need their own Python 3.12 virtualenv; the
Flask app keeps its own.

```bash
cd appraisal_system/appraisal            # or form_builder
python3 -m venv .venv && .venv/bin/pip install -r test-requirements.txt
DATABASE_URL=postgresql://... .venv/bin/flask --app main db upgrade     # release step, never on start-up
PORT=5102 .venv/bin/gunicorn --config gunicorn.conf.py main:app          # form_builder: PORT=5101

# the Flask app, from the repository root
python -m flask --app run:app run --port 5100
```

Point the Flask app's `APPRAISAL_SERVICE_URL` / `FORM_BUILDER_SERVICE_URL` and the services'
`ACCESS_SERVICE_URL` / `FORM_BUILDER_SERVICE_URL` at those ports. The browser API is then
`/api/appraisal/...` and `/api/form-builder/...` on the Flask app.

After changing a service's models, generate the migration (never write one by hand):
`DATABASE_URL=postgresql://... .venv/bin/flask --app main db migrate -m "describe the change"`.
Each service's `migrations/env.py` only touches its own schema, so the Flask app's tables in the
same database are never affected.

## Tests

```bash
cd appraisal_system/form_builder && .venv/bin/pytest     # SQLite in memory
cd appraisal_system/appraisal && .venv/bin/pytest
cd appraisal_system/common && ../appraisal/.venv/bin/pytest
TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/scratch .venv/bin/pytest   # same suites on Postgres

pytest            # from the repository root: the Flask app's suite, including access_bff/tests
```

`appraisal_system/conftest.py` keeps the root `pytest` out of the microservices and `common/`,
which have their own dependencies. Never point `TEST_DATABASE_URL` at Supabase: the suites drop
and recreate tables.

Formatting: Black 23.12.1, then Ruff 0.8.3, at 100 columns (`ruff.toml`).

## Database set-up (Supabase, once per environment)

The services create their tables through migrations; a database admin creates the schemas and
login roles first:

```sql
create schema if not exists forms;
create schema if not exists appraisal;

-- one login role per service; keep the passwords in the platform secret manager
create role hph_form_builder login password '<from secret manager>';
create role hph_appraisal login password '<from secret manager>';

grant usage, create on schema forms to hph_form_builder;
grant usage, create on schema appraisal to hph_appraisal;

revoke all on schema forms, appraisal from public, anon, authenticated;
```

Supabase only serves schemas listed under Settings → API → Exposed schemas; never add `forms` or
`appraisal` there. After the first migration, enable RLS with no policies as a second guard (the
owning role is unaffected):

```sql
do $$
declare t record;
begin
  for t in select schemaname, tablename from pg_tables where schemaname in ('forms', 'appraisal')
  loop
    execute format('alter table %I.%I enable row level security', t.schemaname, t.tablename);
  end loop;
end $$;
```

## Deploying

- Build from the repository root: `docker build -f appraisal_system/appraisal/Dockerfile .` and
  `docker build -f appraisal_system/form_builder/Dockerfile .`. Each Dockerfile's
  `Dockerfile.dockerignore` limits the build to that service and `common/`.
- Run both as private services (Render type `pserv`), reachable only by the Flask app and each
  other; only the Flask app is public.
- Release order: database set-up above, `flask --app main db upgrade` for each service, deploy
  the services, then the Flask app (its migrations add the feature seeds and identity tables).
- Schedule two jobs from the Appraisal image (e.g. Render cron jobs): `flask --app main
  queue-reminders` daily and `flask --app main send-notifications` every few minutes.
  `send-notifications` does nothing until `NOTIFICATIONS_ENABLED=true`.
- Key rotation: publish the new public key in every `SERVICE_AUTH_TRUSTED_ISSUERS`, then swap the
  private key; tokens live 60 seconds.

## Adding an endpoint

1. Describe it in the service's YAML with `operationId: handlers.<module>.<function>`.
2. Write a thin handler: `current_caller()`, call a function in `services/`, serialize, return
   `(payload, status)`.
3. Put rules and database work in `services/`; raise `common.errors` (`InvalidArgument`,
   `FailedPrecondition`, `PermissionDenied`, `NotFound`, ...) to answer with an error.
4. Add tests through the Flask test client with tokens from the `tokens` fixture.

## Not done yet

- The appraisal frontend (form builder UI, form renderer, inbox, cycle admin, progress).
- Real question content for the default form templates.
- Peer and stakeholder reviews (FR-17, FR-18).
- Microsoft sign-in needs HPH's app registration before it can be switched on (see the IT request).
