# Requirement coverage

This page maps every item from the project brief to the code and configuration that implements it, and
to a check you can run yourself. Everything listed as verified was exercised on the local Docker Compose
stack; the exceptions are called out in the last column.

## Platform, Prometheus and exporters

| Requirement | Where it is implemented | How it is verified |
| --- | --- | --- |
| A running server and an application to monitor | The Compose stack itself is the monitored application: Nginx reverse proxy, FastAPI API, React UI and PostgreSQL, plus a Windows desktop registered from the UI. Any Docker host can run the same stack. | `docker compose ps` reports 12 long-running services (plus a one-shot init container); the Windows host is a live Prometheus target. |
| Install Prometheus | `prometheus` service (`prom/prometheus:v3.5.0`) in `docker/docker-compose.yml`; config in `monitoring/prometheus/prometheus.yml`. | `promtool check config`; `/api/v1/targets` shows every job. |
| Scrape intervals, endpoints and retention | Global `scrape_interval: 30s`, cAdvisor 15s, probes 30s; jobs for `prometheus`, `infrawatch`, `nginx`, `node`, `cadvisor`, `process`, `windows` and four `blackbox-*` jobs. Retention is `PROMETHEUS_RETENTION` (default `15d`). | `promtool check config`; target list; `--storage.tsdb.retention.time` in the running process. |
| System metrics exporters (CPU, memory, disk, network) | Node Exporter per registered Linux host, windows_exporter per Windows host, cAdvisor for Docker containers. | `up{job="windows"} == 1` on this machine; Node/cAdvisor jobs proven by `scripts/verify_backend_runtime.py` and `scripts/verify_integrations_runtime.py`. |
| Service exporters (Nginx, MySQL, MongoDB, …) | Nginx is built in: `nginx-prometheus-exporter` scrapes `/nginx_status` on an internal-only 8081 listener. Database exporters are not shipped because this stack has no MySQL/MongoDB service — see [adding a database exporter](#adding-a-database-exporter). | `up{job="nginx"} == 1`, `nginx_http_requests_total` and `nginx_connections_active` on the Application Metrics dashboard. |
| Custom exporters or application instrumentation | The FastAPI API exposes `/metrics` with request, latency, inventory, alert and discovery metrics (`backend/app/core/metrics.py`). `scripts/mock_exporter.py` provides a synthetic exporter for integration tests. | `backend/tests/test_application_metrics.py`; job `infrawatch` target up; API panels populated. |
| PromQL analysis | Every dashboard uses PromQL; the API's Metrics Explorer builds PromQL for host resources; Grafana Explore is available for ad-hoc queries. | Dashboard panels executed through Grafana; recorded series exist. |

## Grafana

| Requirement | Where it is implemented | How it is verified |
| --- | --- | --- |
| Install Grafana and connect Prometheus | `grafana` service plus provisioned datasources `Prometheus` and `Loki` in `monitoring/grafana/provisioning/datasources/`. | `/api/datasources` lists both. |
| System overview dashboard | `infrawatch-overview`, `linux-server-details` and `windows-server-details`. | Panels return data (17 checks in `scripts/verify_windows_dashboard.py` for Windows). |
| Application-specific dashboard | `application-metrics`: throughput, 5xx ratio, p95 latency, per-route breakdown, inventory, Nginx traffic and discovery age. | Every panel query executed through the Grafana datasource. |
| Custom panels using PromQL | Top-routes and per-route latency panels use `topk`/`histogram_quantile`; a text panel lists copyable queries. | Panel queries return series; recording rules produce values. |
| User authentication and authorization | Grafana admin credentials come from `GRAFANA_ADMIN_USER`/`GRAFANA_ADMIN_PASSWORD`; anonymous access and sign-ups are disabled (`GF_AUTH_ANONYMOUS_ENABLED=false`, `GF_USERS_ALLOW_SIGN_UP=false`). Grafana's own admin/viewer roles apply to any additional users you create. | `/grafana/login` requires credentials; dashboards redirect unauthenticated users. |

## Alerting, recording rules and discovery

| Requirement | Where it is implemented | How it is verified |
| --- | --- | --- |
| Prometheus alerting rules | `infra-alerts.yml` (5), `integration-alerts.yml` (20) and `application-alerts.yml` (5): exporter availability, CPU/memory/disk, containers, probes, processes, Windows, API errors/latency and Nginx. | `promtool check rules`; `/api/v1/rules` shows 30 alerting rules. |
| Notification channels (email, Slack) | Alertmanager routing exists (`monitoring/alertmanager/alertmanager.yml`) but deliberately ships with a `local-only` receiver. Delivery needs your SMTP or Slack credentials, which cannot be invented or committed. See [enabling notifications](#enabling-notifications). | Alertmanager UI shows groups; configure a receiver to test delivery. |
| Recording rules | `monitoring/prometheus/rules/recording-rules.yml`: host CPU/memory/filesystem, Windows CPU, probe success, API throughput/error ratio/latency, per-route throughput and Nginx request rate. | `promtool check rules` (10 rules); panels query the recorded series. |
| Service discovery for dynamic environments | The API publishes eight `file_sd` snapshots atomically (`backend/app/services/discovery.py`); Prometheus reloads them every 30s without a restart. New servers, integrations and endpoints appear automatically. | Registration test in `scripts/verify_integrations_runtime.py`; target appears and disappears without a Prometheus restart. |

## Logs and correlation

| Requirement | Where it is implemented | How it is verified |
| --- | --- | --- |
| Loki log aggregation | `loki` service with 7-day retention; Alloy ships Docker, Nginx and host logs with `host`, `service`, `container`, `source` and `level` labels. | `scripts/verify_integrations_runtime.py` reads streams from Loki. |
| Correlate logs with metrics | Grafana provides both datasources; dashboards share `host`/`instance` context, and Explore can pivot from a metric spike to that host's logs by label. | Loki labels and Prometheus labels both include the host identity used by the dashboards. |

## Enabling notifications

Alertmanager cannot read environment variables directly, so notification credentials are added to a local,
uncommitted copy of the configuration:

```yaml
receivers:
  - name: email
    email_configs:
      - to: "oncall@example.com"
        from: "alertmanager@example.com"
        smarthost: "smtp.example.com:587"
        auth_username: "alertmanager@example.com"
        auth_password: "<app-password>"

  - name: slack
    slack_configs:
      - api_url: "https://hooks.slack.com/services/<workspace>/<channel>/<token>"
        channel: "#alerts"
```

Then point the route (or the critical sub-route) at that receiver and restart Alertmanager:

```sh
docker compose restart alertmanager
```

Keep the real file out of version control, or mount it from a secret store. `docs/security.md` covers the
handling rules; never paste webhook URLs or SMTP passwords into issues, screenshots or chat.

## Adding a database exporter

No MySQL or MongoDB runs in this stack, so those exporters are not started by default. The pattern is the
same for any of them (for example `postgres_exporter` against the bundled database):

1. Create a read-only monitoring user in the database and pass its DSN through Compose environment
   variables from `.env` (never in the file itself).
2. Add the exporter service on the `infra` network, listening on a port already allowed by
   `MONITORING_ALLOWED_PORTS` (extend the list if needed).
3. Add a static scrape job in `prometheus.yml`, or attach it as a server integration if it runs on a
   registered host.
4. Restart Prometheus and confirm `up{job="..."} == 1` before building panels.

## Deliberately out of scope

- InfraWatch never installs agents over SSH or executes remote commands; every exporter is installed by the
  operator using the guides in [monitoring integrations](integrations.md).
- No cloud-provider service discovery is configured. File-based discovery covers dynamic registration
  without requiring cloud credentials; add an `ec2_sd_config` or `docker_sd_config` block if needed.
- Public HTTPS/TLS and DNS probes remain unverified on this machine because its containers have no outbound
  DNS or Internet access.
