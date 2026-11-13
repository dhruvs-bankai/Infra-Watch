import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock
from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload, sessionmaker

from app.models import EndpointMonitor, OutboxEvent, Server, ServerIntegration
from app.services.integration_catalog import BY_KIND


DISCOVERY_FILES: dict[str, str] = {
    "node": "servers.json",
    "cadvisor": "cadvisor.json",
    "process_exporter": "process.json",
    "windows_exporter": "windows.json",
    "http": "probes-http.json",
    "https": "probes-http.json",
    "tcp": "probes-tcp.json",
    "dns": "probes-dns.json",
    "icmp": "probes-icmp.json",
}

_PROBE_JOB = {"http": "blackbox-http", "https": "blackbox-http", "tcp": "blackbox-tcp", "dns": "blackbox-dns", "icmp": "blackbox-icmp"}


def _address(host: str) -> str:
    return f"[{host}]" if ":" in host else host


def _target(server: Server) -> dict:
    labels = {
        "instance": server.name,
        "environment": server.environment,
        "server_id": str(server.id),
        "tags": ",".join(sorted(tag.name for tag in server.tags)),
    }
    return {"targets": [f"{_address(server.resolved_address)}:{server.exporter_port}"], "labels": labels}


def build_snapshot(servers: list[Server]) -> list[dict]:
    """Node Exporter targets. Windows hosts are scraped by the windows job instead."""

    return [
        _target(server)
        for server in sorted(servers, key=lambda item: (item.name.casefold(), str(item.id)))
        if server.enabled and server.platform != "windows"
    ]


def _integration_entry(integration: ServerIntegration, server: Server) -> dict:
    definition = BY_KIND.get(integration.kind)
    labels = {
        "instance": server.name,
        "server_id": str(server.id),
        "environment": server.environment,
        "integration": integration.kind,
        "metrics_path": integration.metrics_path,
        "tags": ",".join(sorted(tag.name for tag in server.tags)),
    }
    if definition is not None:
        labels["category"] = definition.category
    return {
        "targets": [f"{_address(server.resolved_address)}:{integration.port}"],
        "labels": labels,
    }


def _probe_entry(monitor: EndpointMonitor, server_names: dict[str, str]) -> dict:
    if monitor.kind in {"http", "https"}:
        target = monitor.target
    elif monitor.kind == "tcp":
        target = f"{_address(monitor.target)}:{monitor.port}"
    else:
        target = monitor.target
    labels = {
        "instance": monitor.name,
        "endpoint_id": str(monitor.id),
        "environment": monitor.environment,
        "probe_kind": monitor.kind,
        "expected_status": str(monitor.expected_status),
        "server": server_names.get(str(monitor.server_id), "") if monitor.server_id else "",
        "server_id": str(monitor.server_id) if monitor.server_id else "",
        "job": _PROBE_JOB[monitor.kind],
    }
    return {"targets": [target], "labels": labels}


def build_snapshots(
    servers: list[Server],
    integrations: list[ServerIntegration],
    endpoints: list[EndpointMonitor],
) -> dict[str, list[dict]]:
    """Build every Prometheus file_sd snapshot, deduplicated per job."""

    by_id = {str(server.id): server for server in servers}
    snapshots: dict[str, list[dict]] = {filename: [] for filename in DISCOVERY_FILES.values()}
    snapshots["servers.json"] = build_snapshot(servers)

    for integration in sorted(integrations, key=lambda item: (item.kind, str(item.id))):
        server = by_id.get(str(integration.server_id))
        if not integration.enabled or server is None or not server.enabled:
            continue
        definition = BY_KIND.get(integration.kind)
        filename = DISCOVERY_FILES.get(integration.kind)
        if definition is None or filename is None:
            continue
        snapshots[filename].append(_integration_entry(integration, server))

    server_names = {str(server.id): server.name for server in servers}
    seen: set[tuple[str, str]] = set()
    for monitor in sorted(endpoints, key=lambda item: (item.kind, item.name.casefold(), str(item.id))):
        if not monitor.enabled:
            continue
        filename = DISCOVERY_FILES.get(monitor.kind)
        if filename is None:
            continue
        entry = _probe_entry(monitor, server_names)
        key = (filename, entry["targets"][0])
        if key in seen:
            continue
        seen.add(key)
        snapshots[filename].append(entry)

    return {filename: entries for filename, entries in snapshots.items()}


def _atomic_write(directory: Path, filename: str, payload: str) -> None:
    descriptor, temporary_path = tempfile.mkstemp(prefix=f".{filename}-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, directory / filename)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def publish_snapshot(
    session_factory: sessionmaker[Session],
    discovery_dir: str,
    *,
    filename: str = "servers.json",
) -> int:
    directory = Path(discovery_dir)
    directory.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(directory / ".discovery.lock"), timeout=10)

    with lock:
        with session_factory() as session:
            servers = list(session.scalars(select(Server).options(selectinload(Server.tags))).all())
            integrations = list(session.scalars(select(ServerIntegration)).all())
            endpoints = list(session.scalars(select(EndpointMonitor)).all())
            pending_ids = list(
                session.scalars(
                    select(OutboxEvent.id).where(OutboxEvent.published_at.is_(None))
                ).all()
            )
            snapshots = build_snapshots(servers, integrations, endpoints)
            payloads = {
                name: json.dumps(entries, ensure_ascii=True, indent=2) + "\n"
                for name, entries in snapshots.items()
            }

        for name, payload in payloads.items():
            _atomic_write(directory, name, payload)
        if os.name != "nt":
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)

        if pending_ids:
            with session_factory.begin() as session:
                session.execute(
                    update(OutboxEvent)
                    .where(OutboxEvent.id.in_(pending_ids), OutboxEvent.published_at.is_(None))
                    .values(published_at=datetime.now(timezone.utc))
                )
        return len(snapshots.get(filename, []))


def add_registry_outbox(session: Session, event_type: str, resource_id: str) -> None:
    session.add(OutboxEvent(event_type=event_type, resource_id=resource_id))


def has_pending_events(session_factory: sessionmaker[Session]) -> bool:
    with session_factory() as session:
        return session.scalar(select(OutboxEvent.id).where(OutboxEvent.published_at.is_(None)).limit(1)) is not None
