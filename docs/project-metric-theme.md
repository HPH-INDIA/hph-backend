# Shared project metric themes

`GET /api/project-metric-theme` returns `{status, message, data: {theme, configured}}`.
`PUT /api/project-metric-theme` accepts exactly `kairon`, `manual`, `adjusted`,
and `target`, each a six-digit hex color including `#`.

The main API uses its normal session and encrypted request/response middleware.
Active Coding users can read; only Coding project managers can save. Project
identity comes from the authenticated user, never a client parameter. Writes
are committed to the main application database on `projects`, with updater and
time recorded. Other browsers poll every 15 seconds and refresh on focus.
No configured theme returns the existing defaults. Line styles and marker shapes
remain fixed. Invalid updates leave the saved theme unchanged.

## Release

Deploy backend before frontend. Run the normal `flask --app run:app db upgrade`
to apply revision `aa10c0d4e201`; the existing start-web script runs it before
Gunicorn starts. No extra service, SQLite database, or theme proxy is needed.
Remove `METRIC_THEME_PROXY_TARGET` from frontend development configuration.
The normal API proxy/base URL serves the endpoint.

Preview SQLite data is not automatically imported into the application database.
A manager can open Color theme, paste their desired colors, and Save project
theme once. All project users then receive these colors after refresh or polling.
A manager's legacy browser colors can prefill settings when no shared theme has
been configured, but the shared database theme always takes precedence.

Run isolated persistence, permissions and validation checks with:
`python -m unittest discover -s tests/unit -p test_project_metric_theme.py`.
These use temporary SQLite databases and the production model/routes. The
PostgreSQL migration should also be validated before release.
