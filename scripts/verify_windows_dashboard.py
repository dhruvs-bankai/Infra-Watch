"""Verify a Windows host and its Grafana dashboard against live data.

Runs every panel query of a provisioned dashboard through the Grafana Prometheus
datasource, then optionally compares key values with ground truth collected from
the Windows host (CIM). Exit code 0 means every check passed.

Usage:
    python scripts/verify_windows_dashboard.py --instance Windows-PC \
        --truth .tmp/host_truth.json

Collect ground truth on the monitored Windows host with:
    powershell -NoProfile -File scripts/collect_windows_truth.ps1 > host_truth.json
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    env_path = ROOT / ".env"
    if not env_path.exists():
        return values
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def grafana_request(path: str, payload: dict[str, Any] | None, user: str, password: str) -> dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode(),
    }
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(f"{GRAFANA}/api{path}", data=body, headers=headers)
    try:
        with OPENER.open(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Grafana {path} returned HTTP {exc.code}: {exc.read().decode()[:300]}") from exc


def prom_query(expr: str) -> list[dict[str, Any]]:
    """Run an instant Prometheus query through Grafana's datasource, like a panel does."""
    payload = {
        "queries": [
            {
                "refId": "A",
                "datasource": {"type": "prometheus", "uid": "prometheus"},
                "expr": expr,
                "instant": True,
            }
        ],
        "from": "now-15m",
        "to": "now",
    }
    result = grafana_request("/ds/query", payload, GRAFANA_USER, GRAFANA_PASSWORD)["results"]["A"]
    if result.get("error"):
        raise RuntimeError(result["error"])
    series: list[dict[str, Any]] = []
    for frame in result.get("frames", []):
        values = frame.get("data", {}).get("values", [])
        if len(values) < 2:
            continue
        sample = values[1][-1] if values[1] else None
        labels = frame.get("schema", {}).get("fields", [{}, {}])[1].get("labels", {})
        if sample is not None:
            series.append({"value": float(sample), "labels": labels})
    return series


def scalar(expr: str) -> float:
    series = prom_query(expr)
    if not series:
        raise RuntimeError(f"no data for {expr}")
    return series[0]["value"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance", default="Windows-PC", help="Prometheus instance label of the Windows host")
    parser.add_argument("--dashboard", default="windows-server-details", help="Grafana dashboard UID")
    parser.add_argument("--truth", help="JSON file with values collected from the Windows host")
    parser.add_argument("--grafana", default="http://127.0.0.1:3000", help="Grafana base URL")
    args = parser.parse_args()

    global GRAFANA, GRAFANA_USER, GRAFANA_PASSWORD
    env = read_env()
    GRAFANA = args.grafana.rstrip("/")
    GRAFANA_USER = env.get("GRAFANA_ADMIN_USER", "admin")
    GRAFANA_PASSWORD = env.get("GRAFANA_ADMIN_PASSWORD", "")

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, condition: bool, detail: str) -> None:
        checks.append((name, condition, detail))

    dashboard = grafana_request(f"/dashboards/uid/{args.dashboard}", None, GRAFANA_USER, GRAFANA_PASSWORD)["dashboard"]
    panels = [panel for panel in dashboard["panels"] if panel.get("type") != "row"]
    check("dashboard loads", True, dashboard.get("title", args.dashboard))
    check("dashboard has expected panels", len(panels) >= 9, f"{len(panels)} panels")

    for panel in panels:
        title = panel.get("title", "untitled")
        exprs = [target["expr"] for target in panel.get("targets", []) if target.get("expr")]
        if not exprs:
            continue
        total = 0
        for expr in exprs:
            expr = expr.replace("$instance", args.instance).replace("${instance}", args.instance)
            expr = expr.replace("$__rate_interval", "5m")
            total += len(prom_query(expr))
        check(f"panel returns data: {title}", total > 0, f"{total} series")

    up = prom_query(f'up{{job="windows",instance="{args.instance}"}}')
    check("host scrape is up", bool(up) and up[0]["value"] == 1, f"up={up[0]['value'] if up else 'missing'}")

    total_memory = scalar(f'windows_memory_physical_total_bytes{{job="windows",instance="{args.instance}"}}')
    available = scalar(f'windows_memory_available_bytes{{job="windows",instance="{args.instance}"}}')
    memory_used_percent = 100 * (1 - available / total_memory)
    panel_memory = scalar(
        f'100 * (1 - (windows_memory_available_bytes{{job="windows",instance="{args.instance}"}} / '
        f'windows_memory_physical_total_bytes{{job="windows",instance="{args.instance}"}}))'
    )
    check(
        "memory utilization math matches panel",
        abs(memory_used_percent - panel_memory) < 0.5,
        f"{panel_memory:.2f}% (recomputed {memory_used_percent:.2f}%)",
    )

    cpu = scalar(
        f'100 * (1 - avg by (instance) (rate(windows_cpu_time_seconds_total'
        f'{{job="windows",instance="{args.instance}",mode="idle"}}[5m])))'
    )
    check("cpu utilization is plausible", 0 <= cpu <= 100, f"{cpu:.2f}%")

    uptime = scalar(f'time() - windows_system_boot_time_timestamp{{job="windows",instance="{args.instance}"}}')
    processes = scalar(f'windows_system_processes{{job="windows",instance="{args.instance}"}}')
    disk_free = scalar(
        f'windows_logical_disk_free_bytes{{job="windows",instance="{args.instance}",volume="C:"}}'
    )
    running_services = prom_query(
        f'windows_service_state{{job="windows",instance="{args.instance}",state="running"}} == 1'
    )
    check("reports running services", len(running_services) > 0, f"{len(running_services)} running services")

    print(f"host={args.instance} memory={total_memory / 1024**3:.2f}GiB available={available / 1024**3:.2f}GiB")
    print(f"cpu={cpu:.2f}% uptime={uptime / 3600:.2f}h processes={processes:.0f} C_free={disk_free / 1024**3:.2f}GiB")

    if args.truth:
        truth = json.loads(Path(args.truth).read_text(encoding="utf-8"))
        truth_total = float(truth["total_memory_bytes"])
        check(
            "total memory matches Windows",
            abs(total_memory - truth_total) / truth_total < 0.02,
            f"exporter {total_memory / 1024**3:.2f}GiB vs Windows {truth_total / 1024**3:.2f}GiB",
        )
        truth_free = float(truth["c_free_bytes"])
        check(
            "C: free space matches Windows",
            abs(disk_free - truth_free) / truth_free < 0.05,
            f"exporter {disk_free / 1024**3:.2f}GiB vs Windows {truth_free / 1024**3:.2f}GiB",
        )
        truth_uptime = float(truth["uptime_seconds"])
        check(
            "uptime matches Windows boot time",
            abs(uptime - truth_uptime) < 600,
            f"exporter {uptime / 3600:.2f}h vs Windows {truth_uptime / 3600:.2f}h",
        )
        truth_processes = float(truth["process_count"])
        check(
            "process count matches Windows",
            abs(processes - truth_processes) / truth_processes < 0.25,
            f"exporter {processes:.0f} vs Windows {truth_processes:.0f}",
        )
        truth_services = float(truth["running_services"])
        check(
            "running service count matches Windows",
            abs(len(running_services) - truth_services) / truth_services < 0.15,
            f"exporter {len(running_services)} vs Windows {truth_services:.0f}",
        )

    failed = [item for item in checks if not item[1]]
    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name} — {detail}")
    print(f"\nSummary: {len(checks) - len(failed)} passed, {len(failed)} failed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
