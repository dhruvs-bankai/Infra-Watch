# How InfraWatch works — plain-language guide

This page explains the project without assuming monitoring experience. Use it to understand the design,
to present the project, or to answer "why are there so many containers?".

## The one-sentence version

> InfraWatch is a self-hosted monitoring platform: you register servers in a web app, Prometheus collects
> their metrics, Grafana turns them into dashboards, Alertmanager raises alerts, and Loki stores logs — all
> started with one `docker compose up` command.

## The flow in one picture

```
Browser → Nginx → React UI
                → FastAPI → PostgreSQL

Prometheus → scrapes exporters on your machines
           → feeds Grafana dashboards and Alertmanager alerts

Alloy → ships logs → Loki → Grafana
```

Think of it as a company:

- **Nginx** is the receptionist — the only public door.
- **React** is the face — what you see and click.
- **FastAPI** is the manager — checks rules, stores servers, talks to monitoring.
- **PostgreSQL** is the filing cabinet — remembers users, servers and alerts.
- **Prometheus** is the inspector — walks around collecting numbers every 30 seconds.
- **Exporters** are the sensors on each machine.
- **Grafana** is the dashboard on the wall.
- **Alertmanager** is the fire alarm — decides who gets told.
- **Loki + Alloy** are the security cameras — record what happened for later.

## Every service: why, what breaks without it, and an alternative

One rule first: **each tool does one job — remove it and you lose exactly that capability.**

### Core application

| Service | Why it exists | If it were missing | Alternative |
| --- | --- | --- | --- |
| **Nginx** | One safe front door on `:8080`. Routes `/api`, `/grafana`, `/alertmanager`, serves the UI, adds security headers. | The API, Grafana and Alertmanager would each need their own exposed port. More open doors, no single entry point, no shared headers. | Caddy or Traefik |
| **Frontend (React)** | The product itself: login, server inventory, charts, integrations, endpoints, alerts. | Only APIs and Grafana would remain — no friendly way to register servers or manage monitoring. | Vue, Svelte, or server-rendered pages |
| **Backend (FastAPI)** | The brain: authentication, validation, saving servers, publishing discovery, querying Prometheus. | Nothing would connect the UI to PostgreSQL or Prometheus. Registration and security checks would not exist. | Django, Express, or Go |
| **PostgreSQL** | Permanent memory for users, servers, integrations, endpoints and alert history. | Everything would reset on restart: no inventory, no login, no history. | MySQL, or SQLite for a small setup |
| **discovery-init** | One-time helper that fixes folder permissions on the shared discovery volume, then exits. | Prometheus might not read the target files, so newly registered servers would not be scraped. | Run the permission fix manually, or bake it into the image |

### Metrics and dashboards

| Service | Why it exists | If it were missing | Alternative |
| --- | --- | --- | --- |
| **Prometheus** | The collector and time-series database. Scrapes every 30 seconds, stores history, evaluates alert and recording rules. | No metrics history. Grafana would have nothing to draw, alerts could not fire, and only "right now" values would exist. | VictoriaMetrics or Grafana Mimir |
| **Grafana** | Visualization: ten dashboards, Explore, and the Prometheus + Loki data sources, behind a login. | You would have raw numbers and JSON, no charts. Explaining server health would be nearly impossible. | Netdata, or Kibana for logs only |
| **Alertmanager** | Decides what happens when an alert fires: grouping, silencing, routing to email/Slack. | Alerts would stay inside Prometheus with no grouping or delivery path. | Grafana Alerting or PagerDuty |
| **nginx-prometheus-exporter** | Converts Nginx's internal counters (requests, connections) into Prometheus metrics. | Grafana could not show Nginx traffic, so you would be blind to front-door load. | Nginx VTS module, or a custom `stub_status` parser |

### Logs

| Service | Why it exists | If it were missing | Alternative |
| --- | --- | --- | --- |
| **Loki** | Cheap log storage, labelled by host, service and severity. | You would run `docker logs` per container. No cross-service search, no "what happened at 14:05?". | Elasticsearch/OpenSearch or ClickHouse |
| **Grafana Alloy** | Collects Docker, Nginx and host logs and ships them to Loki. | Loki would be empty and logs would stay trapped on each machine. | Fluent Bit, Vector, or Promtail (older) |

### Sensors (exporters)

| Service | Why it exists | If it were missing | Alternative |
| --- | --- | --- | --- |
| **Node Exporter** | Linux sensor for CPU, memory, disk, network and load. | Linux hosts would have no system metrics, leaving dashboards and resource alerts empty. | Telegraf, Zabbix agent, or Netdata |
| **windows_exporter** | Windows equivalent, plus services and per-process data. | Windows machines would not appear in monitoring: no Windows dashboard or service alerts. | Telegraf on Windows, or Zabbix agent |
| **cAdvisor** | Container sensor: CPU, memory, network, restarts and limits per Docker container. | No container dashboard, and no way to tell which container is eating CPU or restarting. | Docker Engine metrics or Netdata |
| **Blackbox Exporter** | Probes websites, APIs, TCP ports, DNS and ping from outside; measures latency and TLS expiry. | No uptime checks, no certificate warnings, no "is my website reachable?" view. | Uptime Kuma, or an external service such as Pingdom |
| **process-exporter** | Watches named Linux process groups: alive, CPU, memory, restarts. | You would know a server is busy, but not which process is responsible, and process-disappearance alerts would be impossible. | Telegraf procstat, or a `ps` + textfile script |

Node Exporter, windows_exporter and process-exporter are installed on the machines being watched; the other
containers are part of the InfraWatch stack.

## How it was built, step by step

1. **Application skeleton** — React UI, FastAPI API and PostgreSQL in Docker Compose.
2. **Safe registration** — the API accepts only approved private networks and expected exporter ports, so it
   cannot be tricked into monitoring arbitrary addresses.
3. **Metrics pipeline** — Prometheus scrapes exporters and stores the time series.
4. **Automatic discovery** — the API writes target files that Prometheus watches, so adding a server needs
   no restart.
5. **Dashboards as code** — Grafana is provisioned with its data sources and ten dashboards, not clicked
   together by hand.
6. **Alerting** — 30 rules cover CPU, memory, disk, exporters, containers, endpoints, processes, Windows and
   the API itself; Alertmanager handles grouping.
7. **Wider coverage** — containers (cAdvisor), websites/endpoints (Blackbox), processes and Windows hosts.
8. **Logs** — Loki and Alloy with host/service/level labels, searchable in Grafana.
9. **Self-monitoring** — the API exposes `/metrics`; recording rules and an Nginx exporter complete the loop.
10. **Proof and documentation** — backend and browser tests, live stack checks, requirement mapping and
    beginner guides.

## Design depth worth noticing

- **Security:** host/IP validation, CIDR and port allowlists, blocked loopback/metadata addresses, no
  redirects, and no SSH or remote command execution.
- **Reliability:** discovery files are written atomically, separated per scrape job, and reconciled from an
  outbox so a failed write cannot corrupt other targets.
- **Observability of the observability tool:** the API's own request rate, error ratio and latency are
  monitored, and Nginx is scraped too.
- **Testing:** 47 backend tests, 40 Prometheus rules validated with `promtool`, mocked and live browser
  workflows, plus a full Compose runtime check.
- **Operations:** health checks, read-only containers, dropped capabilities, pinned image versions and
  documented backups/limitations.

## Why not one giant tool?

Because each tool does one job well. Prometheus only collects numbers, Grafana only draws them, Loki only
stores logs, and Alertmanager only decides who to notify. Keeping them separate means one failure does not
take everything down, and any piece can be replaced without rewriting the app.

## The 30-second demo script

> "This is InfraWatch. I open the web app, register a server with its IP, and it appears in Prometheus without
> a restart. Grafana shows CPU, memory, disk, containers, websites and logs across ten dashboards. If
> something crosses a threshold, an alert fires in Alertmanager and is saved in the app. One
> `docker compose up` starts everything, and it works with Linux and Windows hosts."

## Where to go next

- [Architecture and trust boundaries](architecture.md)
- [Requirement coverage](requirements-map.md)
- [Monitoring integrations guide](integrations.md)
- [Beginner setup guide](beginners-guide.md)
- [Back to the README](../README.md)
