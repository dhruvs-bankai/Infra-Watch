"""Run a live API/PostgreSQL/Prometheus discovery smoke against the Compose stack.

Run from the repository root after `docker compose ... up -d`. This creates
three temporary Node Exporter containers on the private Compose network. On a
fresh database, it also provisions a random administrator and stores its
credentials only in the ignored local .env file.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
import os
import secrets
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
COMPOSE = ["docker", "compose", "--env-file", str(ENV_FILE), "-f", "docker/docker-compose.yml"]
API = "http://127.0.0.1:8000"
NODES = [f"infrawatch-test-node-{index}" for index in range(1, 4)]
EXPORTER_IMAGE = "prom/node-exporter:v1.9.1"


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def write_env_value(key: str, value: str) -> None:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    prefix = f"{key}="
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = prefix + value
            break
    else:
        lines.extend(["", "# Created by scripts/verify_backend_runtime.py", prefix + value])
    temporary = ENV_FILE.with_suffix(".env.tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temporary, ENV_FILE)


def request_json(
    path: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    token: str | None = None,
    auth: str | None = None,
    base_url: str = API,
):
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if auth:
        headers["Authorization"] = "Basic " + base64.b64encode(auth.encode("utf-8")).decode("ascii")
    req = urllib.request.Request(base_url + path, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=10) as response:
            return response.status, json.loads(response.read()) if response.status != 204 else {}
    except urllib.error.HTTPError as error:
        body = error.read()
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {"detail": body.decode("utf-8", errors="replace")[:300]}
        return error.code, parsed


def run(*args: str, capture: bool = True) -> str:
    result = subprocess.run(args, cwd=ROOT, check=True, capture_output=capture, text=True)
    return result.stdout.strip() if capture else ""


def start_exporters() -> list[str]:
    started: list[str] = []
    network = json.loads(run("docker", "network", "inspect", "infrawatch_infra", "--format", "{{json .IPAM.Config}}"))
    if not network:
        raise RuntimeError("The InfraWatch Compose network is missing; start the stack first")
    for name in NODES:
        existing = subprocess.run(["docker", "inspect", name], cwd=ROOT, capture_output=True, text=True)
        if existing.returncode == 0:
            raise RuntimeError(f"Container {name} already exists; remove that test container and retry")
        run(
            "docker", "run", "-d", "--rm", "--name", name,
            "--network", "infrawatch_infra", "--network-alias", name,
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            EXPORTER_IMAGE,
        )
        started.append(name)
    return started


def discovery_snapshot() -> list[dict]:
    code = "import json,pathlib; print(json.dumps(json.loads(pathlib.Path('/var/lib/infrawatch/file_sd/servers.json').read_text())))"
    output = run(*COMPOSE, "exec", "-T", "backend", "python", "-c", code)
    return json.loads(output)


def prometheus_up() -> dict[str, str]:
    query = urllib.parse.quote('up{job="node"}', safe="")
    url = f"http://127.0.0.1:9090/api/v1/query?query={query}"
    output = run(*COMPOSE, "exec", "-T", "prometheus", "wget", "-qO-", url)
    result = json.loads(output)
    if result.get("status") != "success":
        raise RuntimeError("Prometheus query failed")
    return {item["metric"].get("instance", ""): item["value"][1] for item in result["data"]["result"]}


def prometheus_node_targets() -> list[str]:
    url = "http://127.0.0.1:9090/api/v1/targets?state=active"
    output = run(*COMPOSE, "exec", "-T", "prometheus", "wget", "-qO-", url)
    result = json.loads(output)
    return [
        target.get("labels", {}).get("instance", "")
        for target in result.get("data", {}).get("activeTargets", [])
        if target.get("labels", {}).get("job") == "node"
    ]


def trigger_alertmanager_smoke() -> None:
    starts_at = datetime.now(timezone.utc)
    payload = [{
        "labels": {"alertname": "InfraWatchRuntimeSmoke", "severity": "info", "instance": "runtime-smoke"},
        "annotations": {"summary": "Temporary alert history integration check", "description": "This test event is sent only to the local disposable Alertmanager."},
        "startsAt": starts_at.isoformat().replace("+00:00", "Z"),
        "endsAt": (starts_at + timedelta(seconds=75)).isoformat().replace("+00:00", "Z"),
        "generatorURL": "http://localhost:8080/",
    }]
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:9093/api/v2/alerts",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=10) as response:
        if response.status not in (200, 202):
            raise RuntimeError(f"Alertmanager rejected the local smoke event (HTTP {response.status})")


def main() -> None:
    if not ENV_FILE.exists():
        raise RuntimeError("Copy .env.example to .env and fill all required secrets before running this check")

    config = read_env()
    admin_name = config.get("INFRAWATCH_ADMIN_USERNAME", "")
    admin_password = config.get("INFRAWATCH_ADMIN_PASSWORD", "")
    new_credentials = not (admin_name and admin_password)
    if new_credentials:
        admin_name = "infrawatch-admin"
        admin_password = secrets.token_urlsafe(36)

    setup_status, setup_body = request_json(
        "/api/v1/auth/setup",
        method="POST",
        payload={
            "setup_token": config["INITIAL_ADMIN_SETUP_TOKEN"],
            "username": admin_name,
            "password": admin_password,
        },
    )
    if setup_status == 201:
        if new_credentials:
            write_env_value("INFRAWATCH_ADMIN_USERNAME", admin_name)
            write_env_value("INFRAWATCH_ADMIN_PASSWORD", admin_password)
            print("Provisioned a random initial admin; credentials saved only in ignored .env")
    elif setup_status == 409 and not new_credentials:
        pass
    else:
        raise RuntimeError(f"Administrator setup failed (HTTP {setup_status}): {setup_body.get('detail')}")

    login_status, login_body = request_json(
        "/api/v1/auth/login",
        method="POST",
        payload={"username": admin_name, "password": admin_password},
    )
    if login_status != 200:
        raise RuntimeError(f"Administrator login failed (HTTP {login_status})")
    token = login_body["access_token"]
    prom_started_at = run("docker", "inspect", "infrawatch-prometheus-1", "--format", "{{.State.StartedAt}}")
    started = start_exporters()
    server_ids: list[str] = []
    try:
        for index, host in enumerate(NODES, start=1):
            status, body = request_json(
                "/api/v1/servers",
                method="POST",
                token=token,
                payload={
                    "name": f"runtime-node-{index}",
                    "host": host,
                    "exporter_port": 9100,
                    "environment": "development",
                    "description": "Temporary Docker integration target",
                    "tags": ["runtime-test", "linux"],
                },
            )
            if status != 201:
                raise RuntimeError(f"Exporter registration {index} failed (HTTP {status}): {body.get('detail')}")
            server_ids.append(body["id"])

        snapshot = discovery_snapshot()
        expected = {f"runtime-node-{index}" for index in range(1, 4)}
        actual = {item["labels"]["instance"] for item in snapshot}
        if not expected.issubset(actual):
            raise RuntimeError(f"Discovery snapshot is missing targets: {sorted(expected - actual)}")

        deadline = time.monotonic() + 100
        scraped: dict[str, str] = {}
        while time.monotonic() < deadline:
            scraped = prometheus_up()
            if all(scraped.get(name) == "1" for name in expected):
                break
            time.sleep(5)
        if not all(scraped.get(name) == "1" for name in expected):
            raise RuntimeError(f"Prometheus did not report all three targets up: {scraped}")

        overview_status, overview = request_json("/api/v1/metrics/overview", token=token)
        if overview_status != 200 or overview.get("online_servers", 0) < 3:
            raise RuntimeError(f"Live metrics overview did not report three online targets: {overview}")
        metric_status, memory_series = request_json(
            f"/api/v1/servers/{server_ids[0]}/metrics?metric=memory_total&hours=1", token=token
        )
        if metric_status != 200 or not memory_series.get("points"):
            raise RuntimeError("The metrics API did not return actual Node Exporter memory samples")
        metric_names = [
            "cpu", "cpu_idle", "cpu_system", "cpu_user", "memory", "memory_total",
            "memory_used", "memory_available", "disk", "disk_total", "disk_used",
            "disk_free", "disk_read", "disk_write", "network_in", "network_out",
            "network_packets_in", "network_packets_out", "network_errors", "load1",
            "load5", "load15", "uptime",
        ]
        for metric_name in metric_names:
            query_status, _ = request_json(
                f"/api/v1/servers/{server_ids[0]}/metrics?metric={metric_name}&hours=1", token=token
            )
            if query_status != 200:
                raise RuntimeError(f"Prometheus rejected the {metric_name} metrics query (HTTP {query_status})")

        rules_url = "http://127.0.0.1:9090/api/v1/rules?type=alert"
        rules_output = run(*COMPOSE, "exec", "-T", "prometheus", "wget", "-qO-", rules_url)
        rules_body = json.loads(rules_output)
        alert_names = {
            rule.get("name")
            for group in rules_body.get("data", {}).get("groups", [])
            for rule in group.get("rules", [])
            if rule.get("type") == "alerting"
        }
        if not {"NodeExporterUnavailable", "NodeCPUUsageHigh", "NodeMemoryUsageHigh", "NodeFilesystemSpaceLow"}.issubset(alert_names):
            raise RuntimeError(f"Prometheus alert rules were not loaded: {sorted(alert_names)}")

        trigger_alertmanager_smoke()
        deadline = time.monotonic() + 60
        stored_alerts = []
        while time.monotonic() < deadline:
            _, alert_body = request_json("/api/v1/alerts", token=token)
            stored_alerts = alert_body.get("items", [])
            if any(item.get("name") == "InfraWatchRuntimeSmoke" for item in stored_alerts):
                break
            time.sleep(3)
        if not any(item.get("name") == "InfraWatchRuntimeSmoke" for item in stored_alerts):
            raise RuntimeError("Alertmanager firing state was not persisted by InfraWatch")

        edited_id = server_ids[0]
        edit_status, edited = request_json(
            f"/api/v1/servers/{edited_id}",
            method="PATCH",
            token=token,
            payload={"environment": "staging", "tags": ["edited", "linux"]},
        )
        if edit_status != 200 or edited["environment"] != "staging":
            raise RuntimeError(f"Live PostgreSQL server edit failed (HTTP {edit_status})")
        changed = {item["labels"]["instance"]: item for item in discovery_snapshot()}
        if changed["runtime-node-1"]["labels"]["environment"] != "staging":
            raise RuntimeError("Server edit was not reflected in the discovery file")

        forbidden_status, _ = request_json(
            "/api/v1/servers",
            method="POST",
            token=token,
            payload={"name": "unsafe-loopback", "host": "127.0.0.1", "exporter_port": 9100},
        )
        if forbidden_status != 422:
            raise RuntimeError(f"Loopback SSRF test returned unexpected HTTP {forbidden_status}")

        grafana_auth = f"{config.get('GRAFANA_ADMIN_USER', 'admin')}:{config['GRAFANA_ADMIN_PASSWORD']}"
        datasource_status, datasources = request_json("/api/datasources", auth=grafana_auth, base_url="http://127.0.0.1:3000")
        if datasource_status != 200 or not any(item.get("name") == "Prometheus" for item in datasources):
            raise RuntimeError("Grafana Prometheus datasource provisioning was not found")
        dashboard_status, dashboard = request_json(
            "/api/dashboards/uid/infrawatch-overview", auth=grafana_auth, base_url="http://127.0.0.1:3000"
        )
        if dashboard_status != 200 or dashboard.get("dashboard", {}).get("title") != "InfraWatch Infrastructure Overview":
            raise RuntimeError("Grafana overview dashboard provisioning was not found")

        current_prom_started_at = run("docker", "inspect", "infrawatch-prometheus-1", "--format", "{{.State.StartedAt}}")
        if current_prom_started_at != prom_started_at:
            raise RuntimeError("Prometheus restarted during target registration")
        for server_id in server_ids:
            delete_status, _ = request_json(f"/api/v1/servers/{server_id}", method="DELETE", token=token)
            if delete_status != 200:
                raise RuntimeError(f"Live PostgreSQL server deletion failed (HTTP {delete_status})")
        server_ids.clear()
        if discovery_snapshot():
            raise RuntimeError("Deleted servers remain in the Prometheus discovery snapshot")
        deadline = time.monotonic() + 45
        remaining_targets = prometheus_node_targets()
        while remaining_targets and time.monotonic() < deadline:
            time.sleep(2)
            remaining_targets = prometheus_node_targets()
        if remaining_targets:
            raise RuntimeError(f"Prometheus retained deleted targets after refresh: {remaining_targets}")
        audit_count = int(run(*COMPOSE, "exec", "-T", "postgres", "psql", "-U", "infrawatch", "-d", "infrawatch", "-tAc", "select count(*) from audit_logs where action like 'server.%'"))
        if audit_count < 7:
            raise RuntimeError(f"Expected create/edit/delete audit rows, found {audit_count}")
        run(*COMPOSE, "restart", "postgres")
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                if request_json("/health/ready")[0] == 200:
                    break
            except (urllib.error.URLError, TimeoutError, OSError):
                pass
            time.sleep(3)
        run(*COMPOSE, "restart", "backend")
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                if request_json("/health/ready")[0] == 200:
                    break
            except (urllib.error.URLError, TimeoutError, OSError):
                pass
            time.sleep(3)
        if request_json("/api/v1/auth/me", token=token)[0] != 200:
            raise RuntimeError("Administrator session did not survive PostgreSQL/backend restarts")
        alert_history = request_json("/api/v1/alerts", token=token)[1].get("items", [])
        if not any(item.get("name") == "InfraWatchRuntimeSmoke" for item in alert_history):
            raise RuntimeError("Alert event history did not survive PostgreSQL/backend restarts")
        audit_after_restart = int(run(*COMPOSE, "exec", "-T", "postgres", "psql", "-U", "infrawatch", "-d", "infrawatch", "-tAc", "select count(*) from audit_logs where action like 'server.%'"))
        if audit_after_restart < audit_count:
            raise RuntimeError("PostgreSQL audit data did not persist across service restarts")
        if discovery_snapshot():
            raise RuntimeError("Prometheus discovery volume did not persist the final target snapshot")
        print("PASS: administrator setup/login, PostgreSQL and alert-history restart persistence, exporter validation, create/edit/delete, audit, atomic discovery, three live scrapes and real metrics, firing alert ingestion, alert-rule loading, SSRF rejection, Grafana provisioning, and no Prometheus restart")
    finally:
        for server_id in server_ids:
            request_json(f"/api/v1/servers/{server_id}", method="DELETE", token=token)
        for name in started:
            subprocess.run(["docker", "stop", name], cwd=ROOT, capture_output=True, text=True, check=False)


if __name__ == "__main__":
    main()
