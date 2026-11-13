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
| `MONITORING_ALLOWED_PORTS` | No | Comma-separated allowed exporter ports; defaults to `9100,8080,9182,9256` (Node Exporter, cAdvisor, windows_exporter, process-exporter). |
| `INFRAWATCH_HOST_LABEL` | No | Host label applied by Grafana Alloy to collected logs; defaults to `infrawatch-host`. |
| `EXPORTER_CONNECT_TIMEOUT_SECONDS` | No | Connectivity validation timeout; defaults to 3 seconds. |
| `CORS_ORIGINS` | No | Exact allowed browser origins for direct cross-origin development. Empty by default for same-origin proxying. |

All services use persistent named volumes for PostgreSQL, Prometheus TSDB, Grafana, Alertmanager, and discovery snapshots. Do not remove volumes to upgrade the application. Back up and restore them using an operator-tested procedure.

## Prometheus file discovery

Prometheus reads eight JSON files from `/etc/prometheus/file_sd/` every 30 seconds from the `prometheus-discovery` named volume. The backend mounts the same volume writable at `/var/lib/infrawatch/file_sd`. It writes every snapshot atomically under a file lock, reconciles a durable outbox, and does not restart Prometheus when registrations change. All files are rebuildable from PostgreSQL.

| File | Job | Contents |
| --- | --- | --- |
| `servers.json` | `node` | Linux Node Exporter targets |
| `cadvisor.json` | `cadvisor` | Container exporter targets |
| `process.json` | `process` | process-exporter targets |
| `windows.json` | `windows` | windows_exporter targets |
| `probes-http.json` | `blackbox-http` | HTTP/HTTPS endpoints |
| `probes-tcp.json` | `blackbox-tcp` | TCP endpoints |
| `probes-dns.json` | `blackbox-dns` | DNS names |
| `probes-icmp.json` | `blackbox-icmp` | ICMP hosts |

Target records include the pinned address plus `instance`, `environment`, `server_id`, `tags` and, for integrations, an `integration` label. Probe targets carry `endpoint_id`, `probe_kind` and `expected_status`. Manual Prometheus configuration edits are not needed for normal registration.

## Metrics and alerts

The authenticated API queries Prometheus at its internal service address (`http://prometheus:9090`) and Alert history collection reads Alertmanager at `http://alertmanager:9093`. These internal addresses can be changed with `PROMETHEUS_URL` and `ALERTMANAGER_URL` in the backend environment for a custom deployment.

Prometheus rule files are mounted from `monitoring/prometheus/rules/`: `infra-alerts.yml` (5 Linux availability and resource rules), `integration-alerts.yml` (20 container, endpoint, process and Windows rules), `application-alerts.yml` (5 API and Nginx rules) and `recording-rules.yml` (10 precomputed series). Validate all four with `promtool check rules` after editing. Alertmanager is provisioned with local-only routing. Configure your own receiver before expecting external notification; never commit SMTP passwords or webhook secrets.

Prometheus scrapes the API's own instrumentation at `http://backend:8000/metrics` (job `infrawatch`) and Nginx through `nginx-prometheus-exporter` at `http://nginx-exporter:9113` (job `nginx`). `/metrics` is served only on the internal network and the loopback API port; Nginx serves `/nginx_status` on an internal-only 8081 listener that is not published or proxied. Prometheus retention is set by `PROMETHEUS_RETENTION` (default `15d`), and all jobs inherit a 30-second scrape interval except cAdvisor at 15 seconds.

Grafana automatically provisions a Prometheus data source, a Loki data source, and ten dashboards (overview, Linux, Windows, containers, endpoints, processes, logs, certificates, integration health, application metrics). Local links use `/grafana/` and `/alertmanager/` through Nginx. Set `INFRAWATCH_PUBLIC_URL` when the visible host differs.

## Centralized logging

Loki listens on the private `infra` network only. Grafana Alloy mounts the Docker socket and host `/var/log` read-only, labels streams with `host`, `service`, `container`, `source` and an extracted `level`, then pushes to `http://loki:3100`. Loki retains 7 days by default. Change retention in `monitoring/loki/loki-config.yml`; the `loki-data` volume holds the data. Loki is not exposed to the host by default and has no built-in authentication — publish it only on a private interface with a firewall if other hosts must ship logs.

## API and tests

The interactive API schema is available at `http://localhost:8000/docs` through the loopback API port. The API offers:

- `POST /api/v1/auth/setup`, `POST /api/v1/auth/login`, `GET /api/v1/auth/me`, `POST /api/v1/auth/logout`
- `GET/POST /api/v1/servers`, `GET/PATCH/DELETE /api/v1/servers/{id}`
- `GET /api/v1/metrics/overview`, `GET /api/v1/metrics/containers`, `GET /api/v1/servers/{id}/metrics?metric=cpu&hours=24`, `GET /api/v1/alerts`
- `GET /api/v1/integrations`, `GET/POST /api/v1/servers/{id}/integrations`, `PATCH/DELETE /api/v1/integrations/{id}`, `POST /api/v1/integrations/{id}/check`
- `GET/POST /api/v1/endpoints`, `PATCH/DELETE /api/v1/endpoints/{id}`, `POST /api/v1/endpoints/{id}/check`
- `GET /health/live`, `GET /health/ready`

Backend tests run with `python -m pytest -q` in `backend/` using SQLite. Frontend checks run in `frontend/` with `npm ci`, `npm run build`, and `npm run test:e2e`. For the real Compose integration run, use `python scripts/verify_backend_runtime.py`; it creates three temporary Node Exporter containers, then cleans up registrations and containers. It also tests migrations, real scrape data, alert rules, Grafana provisioning, persistence across backend/database restarts, SSRF rejection, and no Prometheus restart.

`python scripts/verify_integrations_runtime.py` exercises the Phase 6 paths against the live stack: catalog, cAdvisor attachment, multi-job discovery, container metrics, HTTP/TCP/DNS/ICMP probe registration and results, 25 alert rules, Loki/Alloy ingestion, Grafana datasources and dashboards, and target removal. It creates one temporary synthetic exporter container on the private network and removes it and every test registry entry afterwards. Public HTTPS/TLS and DNS probes may be skipped when the Docker host has no outbound Internet access or its embedded resolver cannot serve direct DNS probes.
