from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import AlertEvent


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _timestamp(value: datetime) -> float:
    normalized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return normalized.timestamp()


def collect_alert_history(session_factory: sessionmaker[Session], alertmanager_url: str) -> int:
    response = httpx.get(
        alertmanager_url.rstrip("/") + "/api/v2/alerts",
        params={"active": "true", "silenced": "true", "inhibited": "true"},
        timeout=5.0,
    )
    response.raise_for_status()
    alerts = response.json()
    now = datetime.now(timezone.utc)
    observed: set[tuple[str, float]] = set()

    with session_factory.begin() as session:
        for alert in alerts:
            labels = alert.get("labels") or {}
            annotations = alert.get("annotations") or {}
            fingerprint = str(alert.get("fingerprint") or "")
            starts_at = _parse_time(alert["startsAt"])
            if not fingerprint:
                continue
            observed.add((fingerprint, _timestamp(starts_at)))
            previous_events = session.scalars(select(AlertEvent).where(AlertEvent.fingerprint == fingerprint)).all()
            event = next((item for item in previous_events if _timestamp(item.started_at) == _timestamp(starts_at)), None)
            if event is None:
                event = AlertEvent(fingerprint=fingerprint, started_at=starts_at)
                session.add(event)
            event.name = str(labels.get("alertname", "Alert"))[:100]
            event.severity = str(labels.get("severity", "info"))[:20]
            event.instance = str(labels.get("instance", ""))[:255]
            event.summary = str(annotations.get("summary", ""))[:1000]
            event.description = str(annotations.get("description", ""))[:2000]
            event.status = "active"
            event.resolved_at = None
            event.last_seen_at = now

        active_events = list(session.scalars(select(AlertEvent).where(AlertEvent.status == "active")).all())
        for event in active_events:
            if (event.fingerprint, _timestamp(event.started_at)) not in observed:
                event.status = "resolved"
                event.resolved_at = now
                event.last_seen_at = now
    return len(observed)
