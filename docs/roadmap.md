# Implementation roadmap and v1.0.0 release gates

## Current delivery

| Phase | Work | Status |
| --- | --- | --- |
| 1 | Repository layout, architecture, initial docs | Complete |
| 2 | Compose, PostgreSQL, Prometheus file discovery, Grafana provisioning, Alertmanager rules | Implemented; local services previously verified healthy |
| 3 | FastAPI, Alembic, authentication, registry, audit, SSRF controls | Implemented; 27 backend tests pass locally |
| 4 | Prometheus metrics API, target health, persisted alert transitions | Implemented; full live verification is in progress |
| 5 | React application, responsive pages, real API client, charts | Implemented; production build and Playwright browser workflow pass locally |
| 6 | API/UI integration, security hardening, automated tests | Implemented in code; full live Compose check is the remaining local verification gate |
| 6b | Advanced monitoring integrations: cAdvisor, Blackbox, process-exporter, windows_exporter, Loki/Alloy, nine dashboards | Implemented and live-verified locally: 47 backend tests, 17 live integration checks, 4 browser workflows (2 mocked, 2 live). Public HTTPS/TLS and DNS probes were skipped because this machine's containers have no outbound DNS/Internet access. |
| 6c | Application instrumentation, recording rules, Nginx exporter, application dashboard and requirement traceability | Implemented and live-verified: API `/metrics` scraped as job `infrawatch`, 10 recording rules, 30 alert rules, Nginx job `nginx`, 10 dashboards, 47 backend tests. |
| 7 | GitHub Actions, operations docs, release assessment | Workflow and docs implemented; remote CI and security scan results require GitHub execution |

The repository is a **release candidate**, not yet an evidence-backed production release. A green local check does not replace TLS, external security review, real operator network tests, backup/restore verification, or successful GitHub CI results.

## Implemented capabilities

- Full Docker Compose stack with persistent Postgres, Prometheus, Grafana, Alertmanager, and file-discovery storage; loopback entry points and health checks.
- FastAPI administrator setup/login/logout, SQLAlchemy models and Alembic migrations, authenticated server CRUD, audit logging, target policy and exporter connectivity checks.
- Atomic Prometheus target generation, durable outbox reconciliation, metrics/overview APIs, metric range queries, and Alertmanager event persistence/resolution tracking.
- Responsive login, dashboard, inventory, create/edit/delete dialogs, server details, metric explorer, active/resolved alerts, installation guide, settings, light/dark mode.
- Focused API tests and Playwright workflow tests; a real Docker-based three-exporter smoke test exercises database/API/discovery/metrics/Grafana/alerts/restarts.
- Monitoring integration registry (`server_integrations`, `endpoint_monitors`) with per-server exporters, endpoint probes, SSRF policy, audit events, reachability checks and live scrape status.
- Multi-job Prometheus discovery with separate atomic files for Node Exporter, cAdvisor, process-exporter, windows_exporter and four blackbox probe types; 25 alert rules validated with `promtool`.
- Grafana Loki and Grafana Alloy collecting Docker, Nginx and host logs with host/service/severity labels; nine provisioned Grafana dashboards.
- GitHub Actions workflows for Pytest, npm audit/build, Playwright, full Compose integration, image builds, and Trivy HIGH/CRITICAL vulnerability gates.

## v1.0.0 gates still to demonstrate

1. Run the full Compose runtime script on the current checkout and retain the actual output for three independent Node Exporters, including real Prometheus metric samples and no restart during registration.
2. Build and start all services from a clean Linux Docker host, confirm the Nginx `:8080` path and Grafana/Alertmanager subpaths, and check user-facing browser errors.
3. Execute GitHub Actions on the release branch and resolve all failing jobs or Trivy findings. Local dependency audit currently reports zero frontend vulnerabilities; this is time-sensitive.
4. Verify TLS and secure remote access behind a production reverse proxy; the included stack binds to loopback and does not provision certificates.
5. Run an operator's real private network registration test and verify host-level TCP/9100 firewalling. The three local test containers do not prove reachability for customer networks.
6. Produce and test restore procedures for PostgreSQL and Prometheus volumes; named persistent volumes alone do not constitute a backup.
7. Review alert receiver configuration, rate limiting, authentication exposure, retention/capacity, dependency updates, and security findings before internet-facing use.
8. Capture reviewed screenshots from real application data, update this checklist with the clean-run evidence, and prepare tagged release notes only after gates pass.
9. Verify public HTTPS/SSL probing from a Docker host with working outbound DNS and Internet access. This machine blocks container egress, so TLS expiry and public DNS checks were skipped (HTTP and TCP probes against internal targets succeeded).
10. Run a DNS check against a normal enterprise resolver. Blackbox's DNS prober could not obtain answers from this machine's Docker embedded resolver, although the probe job and target were generated correctly.
11. Validate a real windows_exporter host on Windows Server. Registration, validation and discovery are covered by tests and the Windows platform path; no Windows host was available here for a live scrape.

## Risks and decisions

- The default exporter allowlist covers broad private address ranges for usability. Deployments must narrow it, and firewalls should independently limit outbound target access.
- The API persists resolution state through a 15-second poll. Alertmanager downtime delays resolution timestamps; it does not fabricate a resolution.
- Prometheus series and label cardinality scale with host count, mount points, and interfaces. Size retention storage and scrape intervals for the deployment.
- There is one administrator role and no rate limiting, MFA, or account recovery flow. Keep remote access behind a trusted TLS and authentication boundary.
- Alert thresholds live in Prometheus rule files; UI-driven threshold changes are not included.
- Docker's standard bridge allows outbound scrapes. The Compose network boundary does not constrain host egress by itself.
- Desktop Docker networking differs from Linux. Production support targets a Linux Docker Engine host; test user-controlled routes and firewalls on that host.

## Dependency policy

- Frontend versions are exact in `frontend/package.json` and `package-lock.json`; CI uses `npm ci` and runs `npm audit`.
- Backend dependencies are exact pins in `backend/requirements.txt` and `requirements-dev.txt`.
- Container tags use fixed version labels rather than `latest`. Add verified digest pins for release builds when the supported multi-architecture matrix is finalized.
- CI runs Trivy on app images with HIGH/CRITICAL findings as failures. Update base images and dependencies promptly, then rerun the full workflow.
