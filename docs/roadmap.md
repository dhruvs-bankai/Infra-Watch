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
| 7 | GitHub Actions, operations docs, release assessment | Workflow and docs implemented; remote CI and security scan results require GitHub execution |

The repository is a **release candidate**, not yet an evidence-backed production release. A green local check does not replace TLS, external security review, real operator network tests, backup/restore verification, or successful GitHub CI results.

## Implemented capabilities

- Full Docker Compose stack with persistent Postgres, Prometheus, Grafana, Alertmanager, and file-discovery storage; loopback entry points and health checks.
- FastAPI administrator setup/login/logout, SQLAlchemy models and Alembic migrations, authenticated server CRUD, audit logging, target policy and exporter connectivity checks.
- Atomic Prometheus target generation, durable outbox reconciliation, metrics/overview APIs, metric range queries, and Alertmanager event persistence/resolution tracking.
- Responsive login, dashboard, inventory, create/edit/delete dialogs, server details, metric explorer, active/resolved alerts, installation guide, settings, light/dark mode.
- Focused API tests and Playwright workflow tests; a real Docker-based three-exporter smoke test exercises database/API/discovery/metrics/Grafana/alerts/restarts.
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
