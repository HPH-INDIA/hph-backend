# Backend Docker Compose

The existing private `.env.render` is the environment file for this stack. Its values are reused unchanged, including the hosted Supabase database and storage credentials. `.env.render.example` documents the required settings but does not contain your actual secrets.

The backend-only Compose file builds directly from [the GitHub repository](https://github.com/VinayVudatalaHPH/hph-backend.git), using its default branch and root Dockerfile. Uncommitted local code is not included. The Docker builder needs access to GitHub; a private repository requires Git authentication configured for the builder.

From the directory containing `compose.yaml` and `.env.render`:

```sh
docker compose up --build -d
docker compose ps
docker compose logs -f backend
docker compose down
```

The backend listens at http://localhost:8081. Compose supplies local HTTP cookie/CORS/login settings and embedded import processing. Copy `.env.compose.example` to `.env.compose` to customize them and pass `--env-file .env.compose` to Compose. For HTTPS use secure cookies and your public frontend URL.

No database container is created. Redis is unnecessary with the existing Render eager Celery setting. Gunicorn starts directly; migrations and storage setup are not automatically executed against Supabase.

To start both frontend and backend, use `docker compose up --build -d` from the sibling `../hph` directory instead. See `../hph/DOCKER.md` for the full stack and optional setup commands. Do not run both stacks independently at the same time.

## Local Linux server

Install Docker Engine with the Compose plugin on the server. For backend-only deployment, copy `compose.yaml`, `.env.compose.example`, and the private `.env.render` file to one directory on the server. The source is fetched from GitHub during the build. For the full stack, keep both repositories as sibling directories. Git does not include `.env.render`. The server needs outbound access to the existing Supabase services.

Copy `.env.compose.example` to `.env.compose` in the directory from which you run Compose. Replace `192.168.1.100` with the Linux server's LAN IP or hostname in `FRONTEND_LOGIN_URL` and `CORS_ALLOWED_ORIGINS`, then run:

```sh
docker compose --env-file .env.compose up --build -d
```

For the full stack, run this from `hph` and open `http://SERVER_IP:8080` on a client machine. The frontend listens on all server interfaces; permit TCP port 8080 in the server firewall for the intended network. The backend port 8081 binds to loopback by default, and Nginx reaches it through the Docker network. For backend-only LAN access, set `BACKEND_BIND_ADDRESS=0.0.0.0` and permit the chosen backend port.

HTTP uses `SESSION_COOKIE_SECURE=false`. If you terminate HTTPS in a reverse proxy, set it to `true` and use the HTTPS frontend URL and origin. The Supabase database and all existing backend secrets remain in `.env.render`; only server-specific settings go in `.env.compose`.

To fetch/build the current GitHub source and recreate the backend:

```sh
docker compose --env-file .env.compose build --pull backend
docker compose --env-file .env.compose up -d backend
```

For repeatable deployments, append `#<commit-sha>` or `#<tag>` to the build context URL.

## Dedicated test environment

Use `Dockerfile.test` and `compose.test.yaml` for the separate `hph-test` stack. See [DOCKER_TEST.md](DOCKER_TEST.md) for setup. The test API reads `.env.test` and keeps PostgreSQL and Storage on Supabase.
