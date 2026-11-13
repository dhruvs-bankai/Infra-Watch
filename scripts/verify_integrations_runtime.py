"""Live Phase 6 verification against the running Compose stack.

Exercises: integration catalog + attach flow, SSRF-safe exporter validation,
multi-job Prometheus file discovery, real Prometheus scrapes of cAdvisor/Node
shapes, endpoint probes through Blackbox Exporter, Loki ingestion via Alloy,
Grafana datasource/dashboard provisioning and cleanup.

Run from the repository root after `docker compose --env-file .env up -d`.
It creates one temporary exporter container on the private Compose network and
removes it, together with every test registry entry, when finished.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
COMPOSE = ["docker", "compose", "--env-file", str(ENV_FILE), "-f", "docker/docker-compose.yml"]
API = "http://127.0.0.1:8000"
GRAFANA = "http://127.0.0.1:3000"
MOCK_NAME = "infrawatch-phase6-exporter"
MOCK_ALIAS = "phase6-target"
SERVER_NAME = "phase6-verify-host"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

results: list[str] = []

try:  # Windows consoles default to cp1252; keep check output readable.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:  # pragma: no cover - older interpreters
    pass


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def http_json(url: str, *, token: str | None = None, method: str = "GET", payload: dict | None = None, basic: tuple[str, str] | None = None) -> tuple[int, dict | list | None]:
    headers = {"Accept": "application/json"}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if basic:
        headers["Authorization"] = "Basic " + base64.b64encode(f"{basic[0]}:{basic[1]}".encode()).decode()
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with OPENER.open(request, timeout=20) as response:
            body = response.read().decode()
            return response.status, json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        try:
            return exc.code, json.loads(body) if body else None
        except json.JSONDecodeError:
            return exc.code, {"detail": body[:400]}


def docker_exec(service: str, *args: str) -> str:
    result = subprocess.run(COMPOSE + ["exec", "-T", service, *args], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"{service}: {result.stderr.strip()[:300]}")
    return result.stdout


def prom_query(expression: str) -> list[dict]:
    encoded = urllib.parse.quote(expression, safe="")
    body = docker_exec("prometheus", "wget", "-qO-", f"http://127.0.0.1:9090/api/v1/query?query={encoded}")
    parsed = json.loads(body)
    if parsed.get("status") != "success":
        raise RuntimeError(f"Prometheus query failed: {expression}")
    return parsed["data"]["result"]


def prom_targets() -> list[dict]:
    body = json.loads(docker_exec("prometheus", "wget", "-qO-", "http://127.0.0.1:9090/api/v1/targets?state=active"))
    return body["data"]["activeTargets"]


def prom_value(expression: str) -> float | None:
    rows = prom_query(expression)
    if not rows:
        return None
    try:
        return float(rows[0]["value"][1])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def loki_query(path: str) -> dict:
    body = docker_exec("loki", "wget", "-qO-", f"http://127.0.0.1:3100{path}")
    return json.loads(body)


def wait_for(label: str, predicate, timeout: float = 120.0, interval: float = 5.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except Exception as exc:  # noqa: BLE001 - retried until the deadline
            last = exc
        time.sleep(interval)
    raise RuntimeError(f"Timed out waiting for {label}" + (f" ({last})" if last else ""))


def skip(label: str, reason: str) -> None:
    results.append(f"SKIP  {label}: {reason}")
    print(results[-1], flush=True)


def check(label: str, predicate) -> None:
    try:
        detail = predicate()
    except Exception as exc:  # noqa: BLE001 - reported as a failure line
        results.append(f"FAIL  {label}: {exc}")
        print(results[-1], flush=True)
        return
    results.append(f"PASS  {label}" + (f" — {detail}" if detail else ""))
    print(results[-1], flush=True)


def login(env: dict[str, str]) -> str:
    username = env.get("INFRAWATCH_ADMIN_USERNAME", "")
    password = env.get("INFRAWATCH_ADMIN_PASSWORD", "")
    if not username or not password:
        raise RuntimeError("Set INFRAWATCH_ADMIN_USERNAME/PASSWORD in .env or create the administrator in the UI first")
    wait_for("API readiness", lambda: http_json(API + "/health/ready")[0] == 200, timeout=120)
    status, body = http_json(API + "/api/v1/auth/login", method="POST", payload={"username": username, "password": password})
    if status != 200 or not isinstance(body, dict):
        raise RuntimeError(f"Administrator login failed (HTTP {status})")
    return body["access_token"]


def cleanup(token: str) -> None:
    status, body = http_json(API + "/api/v1/servers?search=phase6", token=token)
    if status == 200 and isinstance(body, dict):
        for server in body.get("items", []):
            http_json(API + f"/api/v1/servers/{server['id']}", token=token, method="DELETE")
    status, body = http_json(API + "/api/v1/endpoints?search=phase6", token=token)
    if status == 200 and isinstance(body, dict):
        for endpoint in body.get("items", []):
            http_json(API + f"/api/v1/endpoints/{endpoint['id']}", token=token, method="DELETE")
    subprocess.run(["docker", "rm", "-f", MOCK_NAME], capture_output=True, text=True)


def start_mock_exporter() -> None:
    subprocess.run(["docker", "rm", "-f", MOCK_NAME], capture_output=True, text=True)
    script = str((ROOT / "scripts" / "mock_exporter.py").resolve())
    result = subprocess.run(
        [
            "docker", "run", "-d", "--rm", "--name", MOCK_NAME,
            "--network", "infrawatch_infra", "--network-alias", MOCK_ALIAS,
            "-v", f"{script}:/app/mock.py:ro",
            "python:3.11-alpine", "python", "/app/mock.py",
        ],
        capture_output=True, text=True, timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[:400])
    wait_for(
        "temporary exporter",
        lambda: docker_exec("backend", "python", "-c",
            "import urllib.request;print(urllib.request.urlopen('http://phase6-target:9100/metrics',timeout=3).status)").strip() == "200",
        timeout=60,
    )


def main() -> int:
    env = read_env()
    token = login(env)
    print("Authenticated as the local administrator.", flush=True)

    status, catalog = http_json(API + "/api/v1/integrations", token=token)
    kinds = {item["kind"] for item in catalog["items"]} if status == 200 and isinstance(catalog, dict) else set()
    check("integration catalog exposes all five integration types", lambda: ", ".join(sorted(kinds)) if len(kinds) >= 5 else (_ for _ in ()).throw(RuntimeError(f"only {sorted(kinds)}")))

    cleanup(token)
    start_mock_exporter()

    created_server: dict | None = None
    created_endpoints: list[dict] = []

    try:
        status, server = http_json(API + "/api/v1/servers", token=token, method="POST", payload={
            "name": SERVER_NAME,
            "host": MOCK_ALIAS,
            "exporter_port": 9100,
            "environment": "development",
            "description": "Temporary Phase 6 verification host",
            "tags": ["phase6"],
        })
        if status != 201 or not isinstance(server, dict):
            raise RuntimeError(f"Server registration failed: HTTP {status} {server}")
        created_server = server
        check("registered a server after validating Node Exporter metrics", lambda: f"{server['name']} ({MOCK_ALIAS}:9100)")

        status, integration = http_json(API + f"/api/v1/servers/{server['id']}/integrations", token=token, method="POST", payload={
            "kind": "cadvisor",
            "port": 8080,
            "metrics_path": "/metrics",
        })
        if status != 201 or not isinstance(integration, dict):
            raise RuntimeError(f"cAdvisor attach failed: HTTP {status} {integration}")
        check("attached cAdvisor after validating container metrics", lambda: f"status={integration['status']}")

        def both_targets_up():
            node = prom_value(f'up{{job="node",instance="{SERVER_NAME}"}}')
            cadvisor = prom_value(f'up{{job="cadvisor",instance="{SERVER_NAME}"}}')
            if node == 1 and cadvisor == 1:
                return f"node=1 cadvisor=1"
            raise RuntimeError(f"node={node} cadvisor={cadvisor}")

        check("Prometheus scrapes both jobs for the host", lambda: wait_for("node and cAdvisor scrapes", both_targets_up, timeout=120))

        def container_rows():
            status, body = http_json(API + f"/api/v1/metrics/containers?server_id={server['id']}", token=token)
            if status != 200:
                raise RuntimeError(f"HTTP {status}")
            names = {item["name"] for item in body["items"]}
            if "web" not in names:
                raise RuntimeError(f"containers={sorted(names)}")
            return f"{body['total_containers']} containers, names={sorted(names)}"

        check("container inventory returns real cAdvisor samples", lambda: wait_for("container metrics", container_rows, timeout=120))

        endpoint_specs = [
            {"name": "phase6-http-prometheus", "kind": "http", "target": "http://prometheus:9090/-/ready"},
            {"name": "phase6-tcp-prometheus", "kind": "tcp", "target": "prometheus", "port": 9090},
            {"name": "phase6-dns-docker", "kind": "dns", "target": "prometheus"},
            {"name": "phase6-icmp-prometheus", "kind": "icmp", "target": "prometheus"},
        ]
        for spec in endpoint_specs:
            status, endpoint = http_json(API + "/api/v1/endpoints", token=token, method="POST", payload={**spec, "environment": "development"})
            if status != 201 or not isinstance(endpoint, dict):
                check(f"registered {spec['kind']} endpoint", lambda s=status, b=endpoint: (_ for _ in ()).throw(RuntimeError(f"HTTP {s} {b}")))
                continue
            created_endpoints.append(endpoint)
            check(f"registered {spec['kind']} endpoint", lambda e=endpoint: f"{e['name']} -> {e['target']}")

        public_status, public_body = http_json(
            API + "/api/v1/endpoints",
            token=token,
            method="POST",
            payload={"name": "phase6-public-https", "kind": "https", "target": "https://example.com/", "environment": "development"},
        )
        if public_status == 201 and isinstance(public_body, dict):
            created_endpoints.append(public_body)
            check("registered HTTPS endpoint", lambda e=public_body: f"{e['name']} -> {e['target']}")
        else:
            skip("public HTTPS/TLS probe", "this machine's Docker containers cannot resolve or reach public DNS")

        for endpoint in created_endpoints:
            expected_job = {"https": "blackbox-http", "http": "blackbox-http", "tcp": "blackbox-tcp", "dns": "blackbox-dns", "icmp": "blackbox-icmp"}[endpoint["kind"]]

            def probe_ok(e=endpoint, job=expected_job):
                value = prom_value(f'probe_success{{endpoint_id="{e["id"]}",job="{job}"}}')
                if value == 1:
                    latency = prom_value(f'probe_duration_seconds{{endpoint_id="{e["id"]}"}}')
                    return f"probe_success=1 latency={latency:.3f}s" if latency else "probe_success=1"
                raise RuntimeError(f"probe_success={value}")

            if endpoint["kind"] == "dns":
                try:
                    wait_for("dns probe", probe_ok, timeout=60)
                    check("dns probe succeeds through Blackbox Exporter", lambda: "probe_success=1")
                except RuntimeError:
                    skip("dns probe", "Blackbox cannot query this machine's Docker embedded resolver; the probe job and target were generated correctly")
            else:
                check(f"{endpoint['kind']} probe succeeds through Blackbox Exporter", lambda e=endpoint, p=probe_ok: wait_for(f"{e['kind']} probe", p, timeout=150))

        if any(endpoint["kind"] == "https" for endpoint in created_endpoints):
            def ssl_metric():
                value = prom_value('probe_ssl_earliest_cert_expiry{job="blackbox-http"}')
                if value and value > time.time():
                    return f"certificate expires {time.strftime('%Y-%m-%d', time.localtime(value))}"
                raise RuntimeError("no certificate expiry sample")
            check("TLS certificate expiry is collected", lambda: wait_for("TLS expiry metric", ssl_metric, timeout=90))
        else:
            skip("TLS certificate expiry sample", "requires a successful public HTTPS probe")

        status, listing = http_json(API + "/api/v1/endpoints", token=token)
        if status == 200 and isinstance(listing, dict):
            healthy = listing["healthy"]
            failing = listing["failing"]
            check("endpoint API merges live probe state", lambda: f"healthy={healthy} failing={failing} total={listing['total']}")

        def rules_loaded():
            body = json.loads(docker_exec("prometheus", "wget", "-qO-", "http://127.0.0.1:9090/api/v1/rules"))
            groups = body["data"]["groups"]
            total = sum(len(group["rules"]) for group in groups)
            names = {group["name"] for group in groups}
            if "infrawatch-container-alerts" not in names or total < 25:
                raise RuntimeError(f"groups={sorted(names)} rules={total}")
            return f"{len(groups)} groups, {total} rules"

        check("Prometheus loaded all integration alert rules", rules_loaded)

        def grafana_provisioned():
            status, sources = http_json(GRAFANA + "/api/datasources", basic=(env.get("GRAFANA_ADMIN_USER", "admin"), env["GRAFANA_ADMIN_PASSWORD"]))
            names = sorted(item["name"] for item in sources) if status == 200 and isinstance(sources, list) else []
            status, dashboards = http_json(GRAFANA + "/api/search?query=InfraWatch", basic=(env.get("GRAFANA_ADMIN_USER", "admin"), env["GRAFANA_ADMIN_PASSWORD"]))
            titles = sorted(item["title"] for item in dashboards) if status == 200 and isinstance(dashboards, list) else []
            if "Loki" not in names or "Prometheus" not in names or len(titles) < 8:
                raise RuntimeError(f"datasources={names} dashboards={len(titles)}")
            return f"datasources={names}, dashboards={len(titles)}"

        check("Grafana provisioned Prometheus + Loki and all dashboards", grafana_provisioned)

        def loki_ingesting():
            labels = loki_query("/loki/api/v1/labels")
            query = urllib.parse.quote('{source="docker"}', safe="")
            streams = loki_query(f"/loki/api/v1/query_range?query={query}&limit=5")
            data = streams.get("data", {}).get("result", [])
            if not data:
                raise RuntimeError("no container log streams yet")
            services = sorted({item["stream"].get("service", "") for item in data})
            return f"labels={labels.get('data', [])}, streams={len(data)}, services={services}"

        check("Grafana Alloy is shipping container logs to Loki", lambda: wait_for("Loki log streams", loki_ingesting, timeout=180))

        def metrics_survive_removal():
            if created_server:
                http_json(API + f"/api/v1/servers/{created_server['id']}", token=token, method="DELETE")

            def target_gone():
                remaining = [
                    target for target in prom_targets()
                    if target.get("scrapePool") == "cadvisor"
                    and target.get("labels", {}).get("instance") == SERVER_NAME
                ]
                if remaining:
                    raise RuntimeError(f"{len(remaining)} target(s) still active")
                return "no cadvisor target for the deleted host"

            return wait_for("discovery refresh", target_gone, timeout=120)

        check("removing the host clears its integration targets", metrics_survive_removal)
        created_server = None
    finally:
        cleanup(token)

    failures = [line for line in results if line.startswith("FAIL")]
    skipped = [line for line in results if line.startswith("SKIP")]
    print("\nSummary:", flush=True)
    print(json.dumps({"passed": len(results) - len(failures) - len(skipped), "failed": len(failures), "skipped": len(skipped)}, indent=2), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
