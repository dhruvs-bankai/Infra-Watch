# Security review

## Authentication and browser session

- No default administrator is shipped. The first administrator is created once using `INITIAL_ADMIN_SETUP_TOKEN`; later attempts return a conflict.
- Passwords are hashed with Argon2id. JWTs are signed with an environment-supplied HMAC key, expire after a bounded period, and are checked against issuer, audience, token type, and a per-user token version. Sign out increments that version.
- The React app keeps the token in tab-scoped `sessionStorage`, sends it as a bearer token, and clears it after an authentication failure. This reduces persistence after browser closure but does not protect a session from same-origin script execution; TLS and a strict same-origin policy remain necessary for remote access.
- There is one administrator role. Rate limiting, account recovery, MFA, and multi-user role management are not implemented. Keep the deployment private or add a trusted TLS/authentication layer before remote use.
- Nginx sets content, framing, referrer, permissions, and CSP headers. API responses are marked no-store. The UI is same-origin and does not require permissive CORS.

## SSRF and exporter egress

- Server input accepts an IP address or hostname, not a URL/scheme/path/userinfo. Host and port validation is performed before network access.
- `MONITORING_ALLOWED_PORTS` defaults to 9100. `MONITORING_ALLOWED_CIDRS` defaults to private IPv4 and IPv6 ULA ranges; replace it with the exact exporter subnets you own.
- Loopback, link-local, multicast, unspecified, reserved, and known cloud metadata addresses are rejected even if configured broadly. All resolved DNS answers must be allowed.
- The API connects to the validated numeric address with proxy environment variables disabled and redirects off. That address is pinned into the Prometheus discovery target, avoiding DNS rebinding between validation and scrape.
- Only a validated Node Exporter, cAdvisor, process-exporter or windows_exporter metrics response can be registered. The expected metric family is checked, so a random HTTP service on an allowed port is rejected.
- Endpoint probes use a separate policy: public destinations are allowed (that is the purpose of external availability monitoring) while loopback, link-local, multicast, reserved, known cloud metadata ranges and non-global ranges outside `MONITORING_ALLOWED_CIDRS` are rejected. URL input accepts only `http`/`https`, rejects embedded credentials and fragments, and is resolved before the monitor is saved.
- Probe targets are handed to Blackbox Exporter, not fetched by the API. The API never follows redirects, uses proxy environment variables, or executes remote commands.
- Use host and cloud firewalls to restrict registration checks and Prometheus scrapes to the monitoring network. Application CIDR checks alone are not an egress firewall.

## Compose boundary and secrets

## Metrics and status endpoints

- The API `/metrics` endpoint has no authentication because Prometheus must scrape it. It is reachable only
  on the private `infra` network and the loopback API port; Nginx does not proxy it. Do not publish it on a
  public interface — the series reveal route names, inventory counts and process resource use.
- Nginx `/nginx_status` is served on an internal-only port 8081, reachable solely on the `web`
  network by `nginx-prometheus-exporter`; it is not published to the host or proxied by port 8080.
- Prometheus, Alertmanager and the exporters have no host ports except Alertmanager's loopback admin port.
  Grafana is reachable only through Nginx, which requires a login.
- Recording rules and dashboards contain no credentials; notification secrets belong in an uncommitted
  Alertmanager configuration or a secret store, never in `monitoring/` files tracked by Git.

- The web port binds to loopback by default. PostgreSQL and Prometheus have no host-published ports. Direct API, Grafana, and Alertmanager ports bind to loopback for local administration.
- The `infra` Docker network contains API, database, and monitoring services. The `web` network connects Nginx and the web services; PostgreSQL is not attached to `web`.
- Frontend and gateway Nginx images run as an unprivileged user with read-only root filesystems, dropped capabilities, no-new-privileges, and bounded temporary filesystems. The backend also runs unprivileged and drops all capabilities.
- Blackbox Exporter drops all capabilities except `NET_RAW` (required for ICMP). Loki and Alertmanager drop all capabilities and use read-only root filesystems. cAdvisor necessarily runs privileged to read host container statistics; it is never published to the host, and it should be treated as a host-level agent.
- Grafana Alloy runs as root because it reads the Docker socket, but mounts it read-only and mounts host logs read-only. Compromise of Alloy is equivalent to read access to container metadata and logs; keep the Docker socket off any untrusted network.
- Required `.env` values have no production defaults. Generate independent random URL-safe secrets and never commit `.env`, tokens, private keys, or Grafana credentials.
- PostgreSQL password is part of the connection URL; use URL-safe hex or URL-encode punctuation. Environment changes do not rotate an initialized database role password by themselves.
- Node Exporter and all other server-side exporters are operator-installed; InfraWatch never asks for SSH passwords or keys and never executes remote commands. Only exporter endpoint metadata (host, port, path) is stored — never credentials.
- Loki is not published to the host and has no authentication in this configuration. If you publish port 3100 for remote Alloy agents, bind it to a private interface and restrict it with a firewall.

## Data and alert history

- PostgreSQL stores user identity, server inventory, audit records, discovery outbox events, and alert events. Prometheus owns metric time series. Grafana dashboards are provisioned from versioned files.
- A background collector reads Alertmanager every 15 seconds and stores firing and resolved transitions. If Alertmanager is unreachable, it does not mark alerts resolved; saved events remain visible until a successful collection resumes.
- Persistent volumes are not backups. Plan off-host PostgreSQL and Prometheus backups, encryption at rest, retention sizing, restore testing, and a recovery procedure.

## Release gates and known limitations

- No TLS certificate or production host exposure is included. Keep the default loopback bind for local use; configure a trusted TLS reverse proxy before remote browser access.
- Login rate limiting, MFA, multi-admin controls, database threshold editing, automated backup/restore, and an independent security review remain release gates for sensitive or internet-facing deployments.
- CI includes dependency audit, image build, Trivy scan, API tests, frontend browser workflows, and an opt-in full Compose integration run. A passing scan is time-bound evidence, not a guarantee that dependencies have no future CVEs.
- Review [configuration.md](configuration.md) and [roadmap.md](roadmap.md) for operational setup and outstanding release evidence.
