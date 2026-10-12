# Backend production deployment

`Dockerfile.production` uses the existing pinned requirements and runs as a non-root user. `deploy/entrypoint.py` constructs the database URL for the internal PostgreSQL container, including safe password encoding, then executes the web, migration or Celery command.

The backend GitHub Actions workflow calls `HPH-INDIA/hph/.github/workflows/production.yml@main`, passing the backend commit SHA. The frontend repository owns the complete production Compose stack, deployment script and operational guide: `hph/deploy/PRODUCTION.md`.

Configure `PRODUCTION_RUNNER_LABELS`, `PRODUCTION_DEPLOY_ROOT`, `PRODUCTION_ENV_FILE` and `DEPLOY_REPO_TOKEN` in this repository as described there. The existing organization runner must already support this repository and run on the production Linux host. No runner changes are made. Publish the frontend reusable workflow and this Dockerfile before activating the backend workflow.

`deploy/migrate.py` applies migrations and replaces the historical default administrator password only if it is still the known default. Existing changed passwords are preserved. Encryption requires the existing `ENCRYPTION_MASTER_KEY` when migrating data.
