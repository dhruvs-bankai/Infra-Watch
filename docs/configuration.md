# Installation and configuration

For the simple install path, see the [beginner setup guide](beginners-guide.md). The root `compose.yaml` includes the maintained service definitions under `docker/`, so run commands from the repository root.

## Start InfraWatch

```sh
sh ./scripts/prepare-env.sh
docker compose config --quiet
docker compose up -d --build
docker compose --env-file .env -f docker/docker-compose.yml ps
```

The UI is at `http://localhost:8080`. Nginx proxies API requests, Grafana, and Alertmanager. Direct API/Grafana/Alertmanager host ports bind to loopback by default. PostgreSQL and Prometheus ports remain private inside Docker networks. Compose health checks gate Nginx startup on frontend, backend, Grafana, and Alertmanager readiness.

Create the first administrator once with the browser setup form and the setup token from `.env`. The form appears only when no administrator exists. No credentials are hardcoded.

## Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `POSTGRES_PASSWORD` | Yes | Database role password. Choose a URL-safe hex value. |
| `POSTGRES_DB` | No | Database name; defaults to `infrawatch`. |
| `POSTGRES_USER` | No | Database role; defaults to `infrawatch`. |
| `GRAFANA_ADMIN_PASSWORD` | Yes | Grafana administrator password. |
| `GRAFANA_ADMIN_USER` | No | Grafana administrator name; defaults to `admin`. |
| `GRAFANA_PORT` | No | Loopback-only admin port; defaults to `3000`. |
| `ALERTMANAGER_PORT` | No | Loopback-only admin port; defaults to `9093`. |
| `API_PORT` | No | Loopback-only direct API port; defaults to `8000`. |
| `INFRAWATCH_PUBLIC_URL` | No | Browser-visible base URL used for Grafana and Alertmanager links; defaults to `http://localhost:8080`. |
| `PROMETHEUS_RETENTION` | No | TSDB retention duration; defaults to `15d`. Size its named volume for your scrape rate and retention. |
| `JWT_SECRET_KEY` | Yes | JWT signing key, minimum 32 characters. |
| `INITIAL_ADMIN_SETUP_TOKEN` | Yes | One-time first-admin setup secret, minimum 32 characters. |
| `MONITORING_ALLOWED_CIDRS` | No | Comma-separated CIDRs allowed for Node Exporter checks. Defaults to common private IPv4 and IPv6 ULA ranges; narrow this to your own exporter networks. |
| `MONITORING_ALLOWED_PORTS` | No | Comma-separated allowed exporter ports; defaults to `9100`. |
| `EXPORTER_CONNECT_TIMEOUT_SECONDS` | No | Connectivity validation timeout; defaults to 3 seconds. |
| `CORS_ORIGINS` | No | Exact allowed browser origins for direct cross-origin development. Empty by default for same-origin proxying. |

All services use persistent named volumes for PostgreSQL, Prometheus TSDB, Grafana, Alertmanager, and discovery snapshots. Do not remove volumes to upgrade the application. Back up and restore them using an operator-tested procedure.

## Prometheus file discovery

Prometheus reads `/etc/prometheus/file_sd/*.json` every 30 seconds from the `prometheus-discovery` named volume. The backend mounts the same volume writable at `/var/lib/infrawatch/file_sd`. It writes `servers.json` atomically under a file lock, reconciles a durable outbox, and does not restart Prometheus when registrations change. The file is rebuildable from PostgreSQL.

Target records include the pinned address plus `instance`, `environment`, `server_id`, and `tags` labels. Manual Prometheus configuration edits are not needed for normal server registration.

## Metrics and alerts

The authenticated API queries Prometheus at its internal service address (`http://prometheus:9090`) and Alert history collection reads Alertmanager at `http://alertmanager:9093`. These internal addresses can be changed with `PROMETHEUS_URL` and `ALERTMANAGER_URL` in the backend environment for a custom deployment.

Prometheus alert definitions live in `monitoring/prometheus/rules/infra-alerts.yml`. They cover unavailable exporters, CPU >85%, memory >90%, filesystem use >90%, and critically low free filesystem capacity. Alertmanager is provisioned with local-only routing. Configure your own receiver before expecting external notification; never commit SMTP passwords or webhook secrets.

Grafana automatically provisions a Prometheus data source and overview dashboard. Local links use `/grafana/` and `/alertmanager/` through Nginx. Set `INFRAWATCH_PUBLIC_URL` when the visible host differs.

## API and tests

The interactive API schema is available at `http://localhost:8000/docs` through the loopback API port. The API offers:

- `POST /api/v1/auth/setup`, `POST /api/v1/auth/login`, `GET /api/v1/auth/me`, `POST /api/v1/auth/logout`
- `GET/POST /api/v1/servers`, `GET/PATCH/DELETE /api/v1/servers/{id}`
- `GET /api/v1/metrics/overview`, `GET /api/v1/servers/{id}/metrics?metric=cpu&hours=24`, `GET /api/v1/alerts`
- `GET /health/live`, `GET /health/ready`

Backend tests run with `python -m pytest -q` in `backend/` using SQLite. Frontend checks run in `frontend/` with `npm ci`, `npm run build`, and `npm run test:e2e`. For the real Compose integration run, use `python scripts/verify_backend_runtime.py`; it creates three temporary Node Exporter containers, then cleans up registrations and containers. It also tests migrations, real scrape data, alert rules, Grafana provisioning, persistence across backend/database restarts, SSRF rejection, and no Prometheus restart.
