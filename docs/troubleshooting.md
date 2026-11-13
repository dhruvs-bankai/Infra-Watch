# Troubleshooting

Run commands from the repository root. Include `--env-file .env -f docker/docker-compose.yml` when using Compose.

## A service is unhealthy

```sh
docker compose --env-file .env -f docker/docker-compose.yml ps
docker compose --env-file .env -f docker/docker-compose.yml logs --tail=150 backend nginx frontend postgres prometheus grafana alertmanager
```

Confirm all required secrets exist in `.env`, ports 8080/8000/3000/9093 are not already occupied, and Docker has enough CPU, memory, and disk. Backend startup runs Alembic migrations before Uvicorn; inspect backend logs for migration or database credential failures.

## The UI says no data or offline

- Registration checks Node Exporter from the backend container. Confirm the host firewall permits TCP/9100 from the Docker host's egress address.
- Narrow CIDR policy mistakes can reject valid hosts. Review `MONITORING_ALLOWED_CIDRS` and `MONITORING_ALLOWED_PORTS` and recreate only the backend service after changing configuration.
- Test Node Exporter locally on its host with `curl --fail http://127.0.0.1:9100/metrics` and from the InfraWatch machine with its private address.
- After registration, wait for discovery refresh and one or more Prometheus scrapes. CPU rates require a sample range; newly registered targets can initially show no CPU sample.
- Check Prometheus targets from the local container: `docker compose --env-file .env -f docker/docker-compose.yml exec -T prometheus wget -qO- http://localhost:9090/api/v1/targets`.

## Metrics API unavailable

The API calls Prometheus at `PROMETHEUS_URL` over the private Compose network. Check Prometheus health and API logs. A missing series is returned as an empty chart or `null`; this is different from a numeric zero.

## Grafana or Alertmanager path errors

Use `http://localhost:8080/grafana/` and `http://localhost:8080/alertmanager/` so browser paths match service subpath configuration. `INFRAWATCH_PUBLIC_URL` must be the externally visible base URL when using a custom hostname. Grafana credentials come from `.env`; an existing persisted Grafana volume retains its stored credential after environment variables change.

## Alerts do not notify externally

Default Alertmanager routing stores/displays alerts locally only. Configure a receiver and its secret through an operator-managed configuration process, then validate and reload Alertmanager. InfraWatch does not assume email credentials. Alert history resolution is collected every 15 seconds when Alertmanager is reachable.

## An integration cannot be attached

Open **Monitoring integrations** and read the error on the attach dialog. Common causes:

- The exporter is not running, or it listens on a different port. Test from the InfraWatch machine with
  `curl --fail http://HOST:PORT/metrics`.
- The port is missing from `MONITORING_ALLOWED_PORTS`. Add it and recreate the backend.
- The host resolves outside `MONITORING_ALLOWED_CIDRS`, or to loopback/link-local/metadata.
- The endpoint answered, but without the expected metric family (for example a web server on port 8080
  instead of cAdvisor). InfraWatch rejects it rather than showing misleading zeroes.

Per-integration installation and verification steps are in the [monitoring integrations guide](integrations.md).

## Application or Nginx panels are empty

- **Application Metrics** needs the `infrawatch` target: check `up{job="infrawatch"}` in Prometheus and
  `curl http://localhost:8000/metrics` on the InfraWatch host. The endpoint is internal-only; Nginx does not
  proxy it.
- **Nginx panels** need `up{job="nginx"} == 1`. Check `docker compose ps nginx-exporter` and
  `docker compose logs nginx-exporter`. The exporter reads `/nginx_status` on an internal-only 8081 listener; it is not exposed
  through the public 8080 proxy.
- Recording-rule series appear one evaluation cycle (30 seconds) after Prometheus starts. If a recorded
  metric is missing, open Prometheus → Status → Rules and confirm the group is healthy.
- The 5xx panels intentionally show `0` when there are no errors; endpoint tables stay empty until at least
  one endpoint is registered.

## Container, endpoint, process or Windows data is missing

- `docker compose logs cAdvisor blackbox loki alloy` shows the new services. All of them have health checks;
  `docker compose ps` should report `healthy`.
- Container pages need cAdvisor attached to the Docker host and at least two scrapes before CPU rates appear.
- Endpoint probes appear after the first 30-second scrape. Query
  `probe_success{instance="Your endpoint name"}` in Grafana → Explore to see the raw sample.
- Windows hosts must be registered with platform **Windows**; they are scraped by the `windows` job, not
  the Linux `node` job.
- Logs require the Docker socket and `/var/log` mounts. Alloy logs a warning if it cannot read them.
- Public URL probes require outbound DNS and TCP/443 from the monitoring containers. If the Docker host
  has no Internet access, only internal targets can be probed.

## Local integration smoke fails

`python scripts/verify_backend_runtime.py` requires a running Docker Compose stack and uses names `infrawatch-test-node-1` through `-3`. Check for stale containers with those exact names before retrying. The script uses and may persist the local administrator in ignored `.env`; keep that file private.

`python scripts/verify_integrations_runtime.py` uses the container name `infrawatch-phase6-exporter` and server/endpoint names prefixed `phase6-`. It removes them on completion. It reports **SKIP** (not failure) for public HTTPS/TLS and DNS probes when the Docker host cannot reach the Internet or its embedded DNS resolver does not answer the DNS prober.
