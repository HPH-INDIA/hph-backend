# Local shared-theme preview

Run the existing business API on port 8083, then run this isolated extension on 8084:

```sh
PYTHONDONTWRITEBYTECODE=1 venv/bin/python local_preview/start.py
```

It reads existing `.env.render` configuration, retains authentication and encrypted payloads, and stores project themes in ignored `local_preview/data/themes.sqlite3`. Only active Coding managers can write; active Coding users can read their project. Keep this file across restarts.

In the frontend local environment set `METRIC_THEME_PROXY_TARGET=http://127.0.0.1:8084`, with the usual `API_PROXY_TARGET=http://127.0.0.1:8083` and `VITE_API_BASE_URL=/api`. Do not commit credentials or runtime data.

This is a local test extension, not a production API deployment. Production needs the authenticated `/api/project-metric-theme` GET/PUT contract implemented against shared durable storage before deploying the project-theme feature. No business database migration is included.

Test without a business database: `venv/bin/python local_preview/test_metric_theme.py`.
