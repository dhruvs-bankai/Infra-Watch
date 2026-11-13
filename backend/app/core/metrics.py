"""Application metrics exposed for Prometheus at /metrics.

The API is scraped by the `infrawatch` job. HTTP metrics use the matched route
template (not the raw path) so label cardinality stays bounded.
"""

from __future__ import annotations

import time
from pathlib import Path

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import AlertEvent, EndpointMonitor, OutboxEvent, Server, ServerIntegration

HTTP_REQUESTS = Counter(
    "infrawatch_http_requests_total",
    "HTTP requests handled by the InfraWatch API.",
    ["method", "route", "status"],
)
HTTP_DURATION = Histogram(
    "infrawatch_http_request_duration_seconds",
    "InfraWatch API request latency in seconds.",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
SERVERS = Gauge("infrawatch_servers_total", "Registered servers by platform and environment.", ["platform", "environment"])
INTEGRATIONS = Gauge("infrawatch_integrations_total", "Attached monitoring integrations by kind and status.", ["kind", "status"])
ENDPOINTS = Gauge("infrawatch_endpoints_total", "Configured endpoint probes by kind and enabled state.", ["kind", "enabled"])
ALERTS_ACTIVE = Gauge("infrawatch_alerts_active", "Alerts currently firing, as retained from Alertmanager.")
DISCOVERY_PENDING = Gauge("infrawatch_discovery_pending_events", "Registry changes waiting to be published to Prometheus.")
DISCOVERY_AGE = Gauge("infrawatch_discovery_file_age_seconds", "Age of each Prometheus file_sd snapshot.", ["file"])
BUILD_INFO = Gauge("infrawatch_build_info", "InfraWatch API build information.", ["version"])


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST


def observe_request(method: str, route: str, status_code: int, duration_seconds: float) -> None:
    HTTP_REQUESTS.labels(method=method, route=route, status=str(status_code)).inc()
    HTTP_DURATION.labels(method=method, route=route).observe(duration_seconds)


def refresh_inventory_metrics(session_factory: sessionmaker[Session], discovery_dir: str) -> None:
    """Refresh inventory gauges and snapshot ages from the database and disk."""

    now = time.time()
    with session_factory() as session:
        SERVERS.clear()
        for platform, environment, count in session.execute(
            select(Server.platform, Server.environment, func.count()).group_by(Server.platform, Server.environment)
        ):
            SERVERS.labels(platform=platform or "unknown", environment=environment or "unknown").set(count)

        INTEGRATIONS.clear()
        for kind, status, count in session.execute(
            select(ServerIntegration.kind, ServerIntegration.status, func.count()).group_by(
                ServerIntegration.kind, ServerIntegration.status
            )
        ):
            INTEGRATIONS.labels(kind=kind, status=status).set(count)

        ENDPOINTS.clear()
        for kind, enabled, count in session.execute(
            select(EndpointMonitor.kind, EndpointMonitor.enabled, func.count()).group_by(
                EndpointMonitor.kind, EndpointMonitor.enabled
            )
        ):
            ENDPOINTS.labels(kind=kind, enabled="true" if enabled else "false").set(count)

        ALERTS_ACTIVE.set(
            session.scalar(select(func.count()).select_from(AlertEvent).where(AlertEvent.status == "active")) or 0
        )
        DISCOVERY_PENDING.set(
            session.scalar(
                select(func.count()).select_from(OutboxEvent).where(OutboxEvent.published_at.is_(None))
            )
            or 0
        )

    directory = Path(discovery_dir)
    DISCOVERY_AGE.clear()
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            DISCOVERY_AGE.labels(file=path.name).set(max(0.0, now - path.stat().st_mtime))
