import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from filelock import FileLock
from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload, sessionmaker

from app.models import OutboxEvent, Server


def _target(server: Server) -> dict:
    address = server.resolved_address
    if ":" in address:
        address = f"[{address}]"
    labels = {
        "instance": server.name,
        "environment": server.environment,
        "server_id": str(server.id),
        "tags": ",".join(sorted(tag.name for tag in server.tags)),
    }
    return {"targets": [f"{address}:{server.exporter_port}"], "labels": labels}


def build_snapshot(servers: list[Server]) -> list[dict]:
    return [_target(server) for server in sorted(servers, key=lambda item: (item.name.casefold(), str(item.id))) if server.enabled]


def publish_snapshot(
    session_factory: sessionmaker[Session],
    discovery_dir: str,
    *,
    filename: str = "servers.json",
) -> int:
    directory = Path(discovery_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target_file = directory / filename
    lock = FileLock(str(directory / ".discovery.lock"), timeout=10)

    with lock:
        with session_factory() as session:
            servers = list(session.scalars(select(Server).options(selectinload(Server.tags))).all())
            pending_ids = list(
                session.scalars(
                    select(OutboxEvent.id).where(OutboxEvent.published_at.is_(None))
                ).all()
            )
            payload = json.dumps(build_snapshot(servers), ensure_ascii=True, indent=2) + "\n"

        descriptor, temporary_path = tempfile.mkstemp(prefix=".servers-", suffix=".json.tmp", dir=directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary_path, 0o644)
            os.replace(temporary_path, target_file)
            if os.name != "nt":
                directory_fd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)

        if pending_ids:
            with session_factory.begin() as session:
                session.execute(
                    update(OutboxEvent)
                    .where(OutboxEvent.id.in_(pending_ids), OutboxEvent.published_at.is_(None))
                    .values(published_at=datetime.now(timezone.utc))
                )
        return len(build_snapshot(servers))


def add_registry_outbox(session: Session, event_type: str, resource_id: str) -> None:
    session.add(OutboxEvent(event_type=event_type, resource_id=resource_id))


def has_pending_events(session_factory: sessionmaker[Session]) -> bool:
    with session_factory() as session:
        return session.scalar(select(OutboxEvent.id).where(OutboxEvent.published_at.is_(None)).limit(1)) is not None
