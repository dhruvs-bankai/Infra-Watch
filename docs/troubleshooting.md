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

## Local integration smoke fails

`python scripts/verify_backend_runtime.py` requires a running Docker Compose stack and uses names `infrawatch-test-node-1` through `-3`. Check for stale containers with those exact names before retrying. The script uses and may persist the local administrator in ignored `.env`; keep that file private.
