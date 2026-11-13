import asyncio
import math
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.dependencies import CurrentUser, get_session
from app.api.monitoring import _query
from app.models import EndpointMonitor, Server, ServerIntegration
from app.schemas.auth import MessageResponse
from app.schemas.integrations import (
    EndpointListResponse,
    EndpointMonitorCreate,
    EndpointMonitorResponse,
    EndpointMonitorUpdate,
    IntegrationCatalogItem,
    IntegrationCatalogResponse,
    ServerIntegrationCreate,
    ServerIntegrationResponse,
    ServerIntegrationUpdate,
)
from app.services.audit import record_audit
from app.services.discovery import add_registry_outbox, publish_snapshot
from app.services.exporter import (
    ExporterUnreachable,
    TargetPolicyError,
    validate_metrics_endpoint,
    validate_probe_target,
)
from app.services.integration_catalog import ALL_INTEGRATIONS, BY_KIND, SERVER_KINDS

router = APIRouter(tags=["integrations"])
SessionDep = Annotated[Session, Depends(get_session)]


async def _publish(request: Request, resource_id: str) -> None:
    try:
        await asyncio.to_thread(
            publish_snapshot,
            request.app.state.session_factory,
            request.app.state.settings.prometheus_discovery_dir,
        )
    except Exception as exc:  # noqa: BLE001 - surfaced as a retryable 503
        request.app.state.logger.exception("Prometheus discovery publication failed for %s", resource_id)
        raise HTTPException(
            status_code=503,
            detail={"message": "Change saved, but Prometheus discovery publication is pending retry"},
        ) from exc


def _instance_response(integration: ServerIntegration, scrape: str = "unknown") -> ServerIntegrationResponse:
    definition = BY_KIND.get(integration.kind)
    return ServerIntegrationResponse(
        id=integration.id,
        server_id=integration.server_id,
        server_name=integration.server.name if integration.server else "",
        kind=integration.kind,
        title=definition.title if definition else integration.kind,
        port=integration.port,
        metrics_path=integration.metrics_path,
        enabled=integration.enabled,
        status=integration.status,
        status_message=integration.status_message,
        scrape_status=scrape,
        last_checked_at=integration.last_checked_at,
        created_at=integration.created_at,
        updated_at=integration.updated_at,
    )


def _scrape_states(request: Request) -> dict[tuple[str, str], str]:
    """Map (kind, server_id) to Prometheus scrape state without failing the request."""

    try:
        rows = _query(request, 'up{job=~"cadvisor|process|windows"}')
    except HTTPException:
        return {}
    states: dict[tuple[str, str], str] = {}
    for row in rows:
        metric = row.get("metric", {})
        kind = metric.get("integration")
        server_id = metric.get("server_id")
        if kind and server_id:
            states[(kind, server_id)] = "up" if row.get("value", [None, "0"])[1] == "1" else "down"
    return states


def _probe_states(request: Request) -> dict[str, dict]:
    """Live blackbox results keyed by endpoint id."""

    try:
        success = _query(request, 'max by (endpoint_id) (probe_success{job=~"blackbox-.*"})')
        latency = _query(request, 'max by (endpoint_id) (probe_duration_seconds{job=~"blackbox-.*"})')
        ssl_expiry = _query(request, 'max by (endpoint_id) (probe_ssl_earliest_cert_expiry{job=~"blackbox-.*"})')
    except HTTPException:
        return {}
    results: dict[str, dict] = {}

    def _collect(rows: list[dict], key: str) -> None:
        for row in rows:
            endpoint_id = row.get("metric", {}).get("endpoint_id")
            try:
                value = float(row["value"][1])
            except (KeyError, TypeError, ValueError, IndexError):
                continue
            if not endpoint_id or not math.isfinite(value):
                continue
            results.setdefault(endpoint_id, {})[key] = value

    _collect(success, "success")
    _collect(latency, "latency")
    _collect(ssl_expiry, "ssl")
    return results


def _endpoint_response(monitor: EndpointMonitor, probe: dict | None) -> EndpointMonitorResponse:
    probe = probe or {}
    success = probe.get("success")
    ssl_value = probe.get("ssl")
    return EndpointMonitorResponse(
        id=monitor.id,
        name=monitor.name,
        kind=monitor.kind,
        target=monitor.target,
        port=monitor.port,
        path=monitor.path,
        expected_status=monitor.expected_status,
        interval_seconds=monitor.interval_seconds,
        timeout_seconds=monitor.timeout_seconds,
        environment=monitor.environment,
        description=monitor.description,
        server_id=monitor.server_id,
        server_name=monitor.server.name if monitor.server else "",
        enabled=monitor.enabled,
        probe_status="healthy" if success == 1 else ("failing" if success == 0 else "unknown"),
        latency_seconds=probe.get("latency"),
        ssl_expires_at=(
            datetime.fromtimestamp(ssl_value, timezone.utc) if ssl_value and ssl_value > 0 else None
        ),
        created_at=monitor.created_at,
        updated_at=monitor.updated_at,
    )


@router.get("/integrations", response_model=IntegrationCatalogResponse)
def integration_catalog(request: Request, user: CurrentUser, session: SessionDep) -> IntegrationCatalogResponse:
    integrations = list(
        session.scalars(select(ServerIntegration).options(selectinload(ServerIntegration.server))).all()
    )
    scrape = _scrape_states(request)
    configured = {kind: 0 for kind in SERVER_KINDS}
    reachable = {kind: 0 for kind in SERVER_KINDS}
    for integration in integrations:
        configured[integration.kind] = configured.get(integration.kind, 0) + 1
        if scrape.get((integration.kind, str(integration.server_id))) == "up":
            reachable[integration.kind] = reachable.get(integration.kind, 0) + 1
    items = [
        IntegrationCatalogItem(
            **definition.as_dict(),
            configured_count=configured.get(definition.kind, 0),
            reachable_count=reachable.get(definition.kind, 0),
        )
        for definition in ALL_INTEGRATIONS
    ]
    instances = [
        _instance_response(integration, scrape.get((integration.kind, str(integration.server_id)), "unknown"))
        for integration in sorted(integrations, key=lambda item: (item.kind, str(item.id)))
    ]
    return IntegrationCatalogResponse(
        items=items,
        instances=instances,
        endpoint_count=session.scalar(select(func.count()).select_from(EndpointMonitor)) or 0,
    )


@router.get("/servers/{server_id}/integrations", response_model=list[ServerIntegrationResponse])
def server_integrations(
    server_id: UUID, request: Request, user: CurrentUser, session: SessionDep
) -> list[ServerIntegrationResponse]:
    server = session.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    integrations = list(
        session.scalars(
            select(ServerIntegration)
            .options(selectinload(ServerIntegration.server))
            .where(ServerIntegration.server_id == server_id)
            .order_by(ServerIntegration.kind)
        ).all()
    )
    scrape = _scrape_states(request)
    return [
        _instance_response(item, scrape.get((item.kind, str(item.server_id)), "unknown"))
        for item in integrations
    ]


async def _validate_integration(request: Request, server: Server, kind: str, port: int, metrics_path: str):
    definition = BY_KIND.get(kind)
    if definition is None or kind not in SERVER_KINDS:
        raise HTTPException(status_code=422, detail="Unsupported monitoring integration")
    settings = request.app.state.settings
    if port not in settings.allowed_ports:
        raise HTTPException(
            status_code=422,
            detail=f"Port {port} is not allowed by monitoring policy. Add it to MONITORING_ALLOWED_PORTS.",
        )
    try:
        return await validate_metrics_endpoint(
            server.host,
            port,
            settings,
            path=metrics_path,
            markers=definition.metric_markers,
            label=definition.title,
        )
    except TargetPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ExporterUnreachable as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{exc}. Install and start the exporter using the official instructions "
                f"({definition.install_url}) before enabling this integration."
            ),
        ) from exc


@router.post(
    "/servers/{server_id}/integrations",
    response_model=ServerIntegrationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_integration(
    server_id: UUID,
    payload: ServerIntegrationCreate,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
) -> ServerIntegrationResponse:
    server = session.scalar(
        select(Server).options(selectinload(Server.tags)).where(Server.id == server_id)
    )
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    if session.scalar(
        select(ServerIntegration.id).where(
            ServerIntegration.server_id == server_id, ServerIntegration.kind == payload.kind
        )
    ):
        raise HTTPException(status_code=409, detail="This integration is already configured for the server")

    await _validate_integration(request, server, payload.kind, payload.port, payload.metrics_path)
    integration = ServerIntegration(
        server_id=server_id,
        kind=payload.kind,
        port=payload.port,
        metrics_path=payload.metrics_path,
        enabled=payload.enabled,
        status="reachable",
        status_message="Exporter responded with the expected metrics",
        last_checked_at=datetime.now(timezone.utc),
    )
    session.commit()
    try:
        with session.begin():
            session.add(integration)
            record_audit(
                session,
                actor_id=user.id,
                action="integration.created",
                resource_type="monitoring_integration",
                resource_id=str(server_id),
                details={"kind": payload.kind, "port": payload.port, "server": server.name},
                request_id=request.state.request_id,
            )
            add_registry_outbox(session, "integration.created", str(integration.id))
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="This integration is already configured for the server") from exc

    await _publish(request, str(integration.id))
    session.refresh(integration)
    integration.server = server
    return _instance_response(integration, "unknown")


@router.patch("/integrations/{integration_id}", response_model=ServerIntegrationResponse)
async def update_integration(
    integration_id: UUID,
    payload: ServerIntegrationUpdate,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
) -> ServerIntegrationResponse:
    integration = session.scalar(
        select(ServerIntegration)
        .options(selectinload(ServerIntegration.server))
        .where(ServerIntegration.id == integration_id)
    )
    if integration is None:
        raise HTTPException(status_code=404, detail="Integration not found")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return _instance_response(integration)
    server = integration.server
    next_port = changes.get("port", integration.port)
    next_path = changes.get("metrics_path", integration.metrics_path)
    next_enabled = changes.get("enabled", integration.enabled)
    checked_at = integration.last_checked_at
    status_value = integration.status
    message = integration.status_message
    if (next_port != integration.port or next_path != integration.metrics_path or (next_enabled and not integration.enabled)):
        await _validate_integration(request, server, integration.kind, next_port, next_path)
        checked_at = datetime.now(timezone.utc)
        status_value = "reachable"
        message = "Exporter responded with the expected metrics"

    session.commit()
    with session.begin():
        integration.port = next_port
        integration.metrics_path = next_path
        integration.enabled = next_enabled
        integration.status = status_value
        integration.status_message = message
        integration.last_checked_at = checked_at
        record_audit(
            session,
            actor_id=user.id,
            action="integration.updated",
            resource_type="monitoring_integration",
            resource_id=str(integration.id),
            details={"kind": integration.kind, "changed_fields": sorted(changes), "enabled": next_enabled},
            request_id=request.state.request_id,
        )
        add_registry_outbox(session, "integration.updated", str(integration.id))

    await _publish(request, str(integration.id))
    session.refresh(integration)
    return _instance_response(integration, "unknown")


@router.delete("/integrations/{integration_id}", response_model=MessageResponse)
async def delete_integration(
    integration_id: UUID, request: Request, user: CurrentUser, session: SessionDep
) -> MessageResponse:
    integration = session.scalar(
        select(ServerIntegration)
        .options(selectinload(ServerIntegration.server))
        .where(ServerIntegration.id == integration_id)
    )
    if integration is None:
        raise HTTPException(status_code=404, detail="Integration not found")
    record_audit(
        session,
        actor_id=user.id,
        action="integration.deleted",
        resource_type="monitoring_integration",
        resource_id=str(integration.id),
        details={"kind": integration.kind, "server": integration.server.name if integration.server else ""},
        request_id=request.state.request_id,
    )
    add_registry_outbox(session, "integration.deleted", str(integration_id))
    session.delete(integration)
    session.commit()
    await _publish(request, str(integration_id))
    return MessageResponse(message="Monitoring integration removed")


@router.post("/integrations/{integration_id}/check", response_model=ServerIntegrationResponse)
async def check_integration(
    integration_id: UUID, request: Request, user: CurrentUser, session: SessionDep
) -> ServerIntegrationResponse:
    integration = session.scalar(
        select(ServerIntegration)
        .options(selectinload(ServerIntegration.server))
        .where(ServerIntegration.id == integration_id)
    )
    if integration is None:
        raise HTTPException(status_code=404, detail="Integration not found")
    server = integration.server
    checked_at = datetime.now(timezone.utc)
    try:
        await _validate_integration(request, server, integration.kind, integration.port, integration.metrics_path)
        status_value = "reachable"
        message = "Exporter responded with the expected metrics"
    except HTTPException as exc:
        status_value = "unreachable"
        message = str(exc.detail)[:500]
    integration.status = status_value
    integration.status_message = message
    integration.last_checked_at = checked_at
    record_audit(
        session,
        actor_id=user.id,
        action="integration.checked",
        resource_type="monitoring_integration",
        resource_id=str(integration.id),
        details={"kind": integration.kind, "status": status_value},
        request_id=request.state.request_id,
    )
    session.commit()
    return _instance_response(integration, "unknown")


def _check_endpoint_duplicate(
    session: Session, *, kind: str, target: str, port: int | None, exclude: UUID | None = None
) -> None:
    query = select(EndpointMonitor.id).where(
        EndpointMonitor.kind == kind,
        EndpointMonitor.target == target,
        EndpointMonitor.port == port,
    )
    if exclude is not None:
        query = query.where(EndpointMonitor.id != exclude)
    if session.scalar(query) is not None:
        raise HTTPException(status_code=409, detail="This endpoint is already monitored")


async def _validate_endpoint(monitor: EndpointMonitor, request: Request) -> str | None:
    settings = request.app.state.settings
    host = monitor.target
    if monitor.kind in {"http", "https"}:
        from urllib.parse import urlsplit

        host = urlsplit(monitor.target).hostname or ""
    try:
        return await validate_probe_target(
            monitor.kind,
            host,
            settings,
            require_resolution=monitor.kind in {"http", "https", "tcp", "icmp"},
        )
    except TargetPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ExporterUnreachable as exc:
        raise HTTPException(
            status_code=422,
            detail=f"{exc}. The endpoint must resolve before it can be monitored.",
        ) from exc


@router.get("/endpoints", response_model=EndpointListResponse)
def list_endpoints(
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    kind: Annotated[str | None, Query(pattern="^(http|https|tcp|dns|icmp)$")] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
) -> EndpointListResponse:
    query = select(EndpointMonitor).options(selectinload(EndpointMonitor.server))
    if kind:
        query = query.where(EndpointMonitor.kind == kind)
    if search:
        pattern = f"%{search.strip()}%"
        query = query.where(or_(EndpointMonitor.name.ilike(pattern), EndpointMonitor.target.ilike(pattern)))
    monitors = list(session.scalars(query.order_by(EndpointMonitor.name)).all())
    probes = _probe_states(request)
    items = [_endpoint_response(monitor, probes.get(str(monitor.id))) for monitor in monitors]
    return EndpointListResponse(
        items=items,
        total=len(items),
        healthy=sum(1 for item in items if item.probe_status == "healthy"),
        failing=sum(1 for item in items if item.probe_status == "failing"),
    )


@router.post("/endpoints", response_model=EndpointMonitorResponse, status_code=status.HTTP_201_CREATED)
async def create_endpoint(
    payload: EndpointMonitorCreate, request: Request, user: CurrentUser, session: SessionDep
) -> EndpointMonitorResponse:
    if session.scalar(select(EndpointMonitor.id).where(EndpointMonitor.name == payload.name)):
        raise HTTPException(status_code=409, detail="An endpoint with this name already exists")
    if payload.server_id is not None and session.get(Server, payload.server_id) is None:
        raise HTTPException(status_code=422, detail="Associated server was not found")
    _check_endpoint_duplicate(session, kind=payload.kind, target=payload.target, port=payload.port)

    monitor = EndpointMonitor(
        name=payload.name,
        kind=payload.kind,
        target=payload.target,
        port=payload.port,
        path=payload.path,
        expected_status=payload.expected_status,
        interval_seconds=payload.interval_seconds,
        timeout_seconds=payload.timeout_seconds,
        environment=payload.environment,
        description=payload.description,
        server_id=payload.server_id,
        enabled=payload.enabled,
    )
    await _validate_endpoint(monitor, request)
    session.commit()
    try:
        with session.begin():
            session.add(monitor)
            record_audit(
                session,
                actor_id=user.id,
                action="endpoint.created",
                resource_type="endpoint_monitor",
                resource_id=str(monitor.id),
                details={"kind": monitor.kind, "target": monitor.target, "port": monitor.port},
                request_id=request.state.request_id,
            )
            add_registry_outbox(session, "endpoint.created", str(monitor.id))
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="This endpoint is already monitored") from exc

    await _publish(request, str(monitor.id))
    session.refresh(monitor)
    return _endpoint_response(monitor, None)


@router.patch("/endpoints/{endpoint_id}", response_model=EndpointMonitorResponse)
async def update_endpoint(
    endpoint_id: UUID,
    payload: EndpointMonitorUpdate,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
) -> EndpointMonitorResponse:
    monitor = session.scalar(
        select(EndpointMonitor).options(selectinload(EndpointMonitor.server)).where(EndpointMonitor.id == endpoint_id)
    )
    if monitor is None:
        raise HTTPException(status_code=404, detail="Endpoint monitor not found")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return _endpoint_response(monitor, None)
    if changes.get("name") and session.scalar(
        select(EndpointMonitor.id).where(EndpointMonitor.name == changes["name"], EndpointMonitor.id != endpoint_id)
    ):
        raise HTTPException(status_code=409, detail="An endpoint with this name already exists")
    if changes.get("server_id") is not None and session.get(Server, changes["server_id"]) is None:
        raise HTTPException(status_code=422, detail="Associated server was not found")

    previous = {"target": monitor.target, "port": monitor.port, "enabled": monitor.enabled}
    for field, value in changes.items():
        setattr(monitor, field, value)
    _check_endpoint_duplicate(session, kind=monitor.kind, target=monitor.target, port=monitor.port, exclude=monitor.id)
    await _validate_endpoint(monitor, request)
    record_audit(
        session,
        actor_id=user.id,
        action="endpoint.updated",
        resource_type="endpoint_monitor",
        resource_id=str(endpoint_id),
        details={"before": previous, "changed_fields": sorted(changes)},
        request_id=request.state.request_id,
    )
    add_registry_outbox(session, "endpoint.updated", str(endpoint_id))
    session.commit()
    await _publish(request, str(endpoint_id))
    session.refresh(monitor)
    return _endpoint_response(monitor, None)


@router.delete("/endpoints/{endpoint_id}", response_model=MessageResponse)
async def delete_endpoint(
    endpoint_id: UUID, request: Request, user: CurrentUser, session: SessionDep
) -> MessageResponse:
    monitor = session.get(EndpointMonitor, endpoint_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail="Endpoint monitor not found")
    record_audit(
        session,
        actor_id=user.id,
        action="endpoint.deleted",
        resource_type="endpoint_monitor",
        resource_id=str(endpoint_id),
        details={"kind": monitor.kind, "target": monitor.target},
        request_id=request.state.request_id,
    )
    add_registry_outbox(session, "endpoint.deleted", str(endpoint_id))
    session.delete(monitor)
    session.commit()
    await _publish(request, str(endpoint_id))
    return MessageResponse(message="Endpoint monitor removed")


@router.post("/endpoints/{endpoint_id}/check", response_model=EndpointMonitorResponse)
def check_endpoint(
    endpoint_id: UUID, request: Request, user: CurrentUser, session: SessionDep
) -> EndpointMonitorResponse:
    monitor = session.scalar(
        select(EndpointMonitor).options(selectinload(EndpointMonitor.server)).where(EndpointMonitor.id == endpoint_id)
    )
    if monitor is None:
        raise HTTPException(status_code=404, detail="Endpoint monitor not found")
    probes = _probe_states(request)
    return _endpoint_response(monitor, probes.get(str(endpoint_id)))
