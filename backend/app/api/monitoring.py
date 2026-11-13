from datetime import datetime, timedelta, timezone
import math
from typing import Annotated, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentUser, get_session
from app.models import AlertEvent, Server

router = APIRouter(tags=["monitoring"])
SessionDep = Annotated[Session, Depends(get_session)]
MetricName = Literal["cpu", "cpu_idle", "cpu_system", "cpu_user", "memory", "memory_total", "memory_used", "memory_available", "disk", "disk_total", "disk_used", "disk_free", "disk_read", "disk_write", "network_in", "network_out", "network_packets_in", "network_packets_out", "network_errors", "load", "load1", "load5", "load15", "uptime"]

PROMQL = {
    "cpu": '100 * (1 - avg by (instance, server_id, environment) (rate(node_cpu_seconds_total{job="node",mode="idle"}[5m])))',
    "cpu_idle": '100 * avg by (instance, server_id, environment) (rate(node_cpu_seconds_total{job="node",mode="idle"}[5m]))',
    "cpu_system": '100 * avg by (instance, server_id, environment) (rate(node_cpu_seconds_total{job="node",mode="system"}[5m]))',
    "cpu_user": '100 * avg by (instance, server_id, environment) (rate(node_cpu_seconds_total{job="node",mode="user"}[5m]))',
    "memory": '100 * (1 - node_memory_MemAvailable_bytes{job="node"} / node_memory_MemTotal_bytes{job="node"})',
    "memory_total": 'node_memory_MemTotal_bytes{job="node"}',
    "memory_used": 'node_memory_MemTotal_bytes{job="node"} - node_memory_MemAvailable_bytes{job="node"}',
    "memory_available": 'node_memory_MemAvailable_bytes{job="node"}',
    "disk": '100 * (1 - min by (instance, server_id, environment) (node_filesystem_avail_bytes{job="node",fstype!~"tmpfs|devtmpfs|overlay|squashfs|nsfs|ramfs"} / node_filesystem_size_bytes{job="node",fstype!~"tmpfs|devtmpfs|overlay|squashfs|nsfs|ramfs"}))',
    "network_in": 'sum by (instance, server_id, environment) (rate(node_network_receive_bytes_total{job="node",device!~"lo|veth.*|docker.*|br-.*"}[5m]))',
    "network_out": 'sum by (instance, server_id, environment) (rate(node_network_transmit_bytes_total{job="node",device!~"lo|veth.*|docker.*|br-.*"}[5m]))',
    "load": 'node_load5{job="node"}',
    "load1": 'node_load1{job="node"}',
    "load5": 'node_load5{job="node"}',
    "load15": 'node_load15{job="node"}',
    "uptime": 'time() - node_boot_time_seconds{job="node"}',
    "disk_total": 'sum by (instance, server_id, environment) (node_filesystem_size_bytes{job="node",fstype!~"tmpfs|devtmpfs|overlay|squashfs|nsfs|ramfs"})',
    "disk_used": 'sum by (instance, server_id, environment) (node_filesystem_size_bytes{job="node",fstype!~"tmpfs|devtmpfs|overlay|squashfs|nsfs|ramfs"} - node_filesystem_avail_bytes{job="node",fstype!~"tmpfs|devtmpfs|overlay|squashfs|nsfs|ramfs"})',
    "disk_free": 'sum by (instance, server_id, environment) (node_filesystem_avail_bytes{job="node",fstype!~"tmpfs|devtmpfs|overlay|squashfs|nsfs|ramfs"})',
    "disk_read": 'sum by (instance, server_id, environment) (rate(node_disk_read_bytes_total{job="node"}[5m]))',
    "disk_write": 'sum by (instance, server_id, environment) (rate(node_disk_written_bytes_total{job="node"}[5m]))',
    "network_packets_in": 'sum by (instance, server_id, environment) (rate(node_network_receive_packets_total{job="node",device!~"lo|veth.*|docker.*|br-.*"}[5m]))',
    "network_packets_out": 'sum by (instance, server_id, environment) (rate(node_network_transmit_packets_total{job="node",device!~"lo|veth.*|docker.*|br-.*"}[5m]))',
    "network_errors": 'sum by (instance, server_id, environment) (rate(node_network_receive_errs_total{job="node",device!~"lo|veth.*|docker.*|br-.*"}[5m]) + rate(node_network_transmit_errs_total{job="node",device!~"lo|veth.*|docker.*|br-.*"}[5m]))',
}


def _prometheus(request: Request, path: str, params: dict | None = None):
    url = request.app.state.settings.prometheus_url.rstrip("/") + path
    try:
        response = httpx.get(url, params=params, timeout=5.0)
        response.raise_for_status()
        body = response.json()
        if body.get("status") != "success":
            raise ValueError("Prometheus returned an unsuccessful query")
        return body.get("data", {})
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Prometheus metrics are temporarily unavailable") from exc


def _query(request: Request, expression: str) -> list[dict]:
    return _prometheus(request, "/api/v1/query", {"query": expression}).get("result", [])


def _number(result: list[dict]) -> float | None:
    if not result:
        return None
    try:
        value = float(result[0]["value"][1])
        return value if math.isfinite(value) else None
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def _values_by_server(rows: list[dict]) -> dict[str, float]:
    values: dict[str, float] = {}
    for row in rows:
        server_id = row.get("metric", {}).get("server_id")
        try:
            value = float(row["value"][1])
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        if server_id and math.isfinite(value):
            values[server_id] = value
    return values


@router.get("/metrics/overview")
def overview(request: Request, user: CurrentUser, session: SessionDep):
    total = session.scalar(select(func.count()).select_from(Server)) or 0
    inventory = list(session.scalars(select(Server).where(Server.enabled.is_(True))).all())
    up = _query(request, 'up{job="node"}')
    up_by_id = {row.get("metric", {}).get("server_id"): row.get("value", [None, "0"])[1] == "1" for row in up}
    cpu = _values_by_server(_query(request, PROMQL["cpu"]))
    memory = _values_by_server(_query(request, PROMQL["memory"]))
    online = sum(1 for server in inventory if up_by_id.get(str(server.id)) is True)
    offline = sum(1 for server in inventory if str(server.id) in up_by_id and up_by_id[str(server.id)] is False)
    online_ids = [str(server.id) for server in inventory if up_by_id.get(str(server.id)) is True]
    cpu_values = [cpu[key] for key in online_ids if key in cpu]
    memory_values = [memory[key] for key in online_ids if key in memory]
    disks = _query(request, 'sum by (server_id) (node_filesystem_size_bytes{job="node",fstype!~"tmpfs|devtmpfs|overlay|squashfs|nsfs|ramfs"})')
    disk_values = [value for server_id, value in _values_by_server(disks).items() if server_id in online_ids]
    network_in = _query(request, PROMQL["network_in"])
    network_out = _query(request, PROMQL["network_out"])
    network_in_values = [value for server_id, value in _values_by_server(network_in).items() if server_id in online_ids]
    network_out_values = [value for server_id, value in _values_by_server(network_out).items() if server_id in online_ids]
    return {
        "total_servers": total,
        "online_servers": online,
        "offline_servers": offline,
        "unknown_servers": max(0, total - online - offline),
        "exporter_errors": offline,
        "avg_cpu_percent": sum(cpu_values) / len(cpu_values) if cpu_values else None,
        "avg_memory_percent": sum(memory_values) / len(memory_values) if memory_values else None,
        "total_disk_bytes": sum(disk_values) if disk_values else None,
        "network_in_bytes_per_second": sum(network_in_values) if network_in_values else None,
        "network_out_bytes_per_second": sum(network_out_values) if network_out_values else None,
        "servers": [{"id": str(server.id), "name": server.name, "environment": server.environment, "status": "online" if up_by_id.get(str(server.id)) is True else ("offline" if str(server.id) in up_by_id else "unknown"), "cpu_percent": cpu.get(str(server.id)), "memory_percent": memory.get(str(server.id))} for server in inventory],
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/servers/{server_id}/metrics")
def server_metrics(
    server_id: UUID,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    metric: MetricName = "cpu",
    hours: Annotated[int, Query(ge=1, le=168)] = 24,
):
    server = session.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    expression = PROMQL[metric].replace('{job="node",', f'{{job="node",server_id="{server_id}",')
    expression = expression.replace('{job="node"}', f'{{job="node",server_id="{server_id}"}}')
    step_seconds = max(60, int(hours * 3600 / 1200))
    data = _prometheus(request, "/api/v1/query_range", {"query": expression, "start": start.timestamp(), "end": end.timestamp(), "step": step_seconds})
    series = []
    for row in data.get("result", []):
        for timestamp, value in row.get("values", []):
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(number):
                continue
            series.append({"timestamp": datetime.fromtimestamp(float(timestamp), timezone.utc).isoformat(), "value": number})
    if metric in {"cpu", "cpu_idle", "cpu_system", "cpu_user", "memory", "disk"}:
        unit = "percent"
    elif metric in {"memory_total", "memory_used", "memory_available", "disk_total", "disk_used", "disk_free"}:
        unit = "bytes"
    elif metric in {"network_in", "network_out", "disk_read", "disk_write"}:
        unit = "bytes_per_second"
    elif metric in {"network_packets_in", "network_packets_out", "network_errors"}:
        unit = "per_second"
    elif metric == "uptime":
        unit = "seconds"
    else:
        unit = "load"
    return {"server_id": str(server.id), "server_name": server.name, "metric": metric, "unit": unit, "points": series}


@router.get("/metrics/containers")
def containers(
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    server_id: Annotated[UUID | None, Query()] = None,
):
    """Live container inventory and resource use from cAdvisor."""

    selector = f'server_id="{server_id}",' if server_id else ""
    base = f'{{job="cadvisor",{selector}name!=""}}'
    queries = {
        "cpu": f'sum by (server_id, name) (rate(container_cpu_usage_seconds_total{base}[5m])) * 100',
        "memory": f'sum by (server_id, name) (container_memory_working_set_bytes{base})',
        "limit": f'max by (server_id, name) (container_spec_memory_limit_bytes{base})',
        "filesystem": f'sum by (server_id, name) (container_fs_usage_bytes{base})',
        "network_in": f'sum by (server_id, name) (rate(container_network_receive_bytes_total{base}[5m]))',
        "network_out": f'sum by (server_id, name) (rate(container_network_transmit_bytes_total{base}[5m]))',
        "restarts": f'changes(container_start_time_seconds{base}[1h])',
        "image": f'max by (server_id, name, image) (container_last_seen{base})',
    }
    rows = {key: _query(request, expression) for key, expression in queries.items()}
    containers_by_key: dict[tuple[str, str], dict] = {}
    for key, results in rows.items():
        for row in results:
            metric = row.get("metric", {})
            name = metric.get("name")
            owner = metric.get("server_id", "")
            if not name:
                continue
            entry = containers_by_key.setdefault((owner, name), {"server_id": owner, "name": name})
            if key == "image":
                entry["image"] = metric.get("image", "")
                continue
            try:
                value = float(row["value"][1])
            except (KeyError, TypeError, ValueError, IndexError):
                continue
            if math.isfinite(value):
                entry[key] = value
    items = sorted(containers_by_key.values(), key=lambda item: item.get("cpu", 0), reverse=True)[:100]
    names = {str(server.id): server.name for server in session.scalars(select(Server)).all()}
    for item in items:
        item["server_name"] = names.get(item["server_id"], "") or "Unassigned"
    return {
        "total_containers": len(containers_by_key),
        "items": items,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/alerts")
def alerts(user: CurrentUser, session: SessionDep):
    events = session.scalars(select(AlertEvent).order_by(AlertEvent.started_at.desc()).limit(200)).all()
    items = [{"fingerprint": event.fingerprint, "name": event.name, "severity": event.severity, "status": event.status, "instance": event.instance, "summary": event.summary, "description": event.description, "started_at": event.started_at.isoformat(), "ends_at": event.resolved_at.isoformat() if event.resolved_at else None} for event in events]
    return {"items": items, "active_count": sum(1 for event in events if event.status == "active"), "history_count": len(items)}

