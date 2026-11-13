import asyncio
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.dependencies import CurrentUser, get_session
from app.models import Server, ServerIntegration, Tag
from app.schemas.auth import MessageResponse
from app.schemas.servers import ServerCreate, ServerListResponse, ServerResponse, ServerUpdate
from app.services.audit import record_audit
from app.services.discovery import add_registry_outbox, publish_snapshot
from app.services.exporter import (
    ExporterUnreachable,
    TargetPolicyError,
    ValidatedTarget,
    validate_exporter,
    validate_metrics_endpoint,
)


router = APIRouter(prefix="/servers", tags=["servers"])
SessionDep = Annotated[Session, Depends(get_session)]


def _response(server: Server) -> ServerResponse:
    return ServerResponse(
        id=server.id,
        name=server.name,
        host=server.host,
        exporter_port=server.exporter_port,
        platform=server.platform,
        environment=server.environment,
        description=server.description,
        tags=[tag.name for tag in server.tags],
        created_at=server.created_at,
        updated_at=server.updated_at,
    )


def _set_tags(session: Session, server: Server, names: list[str]) -> None:
    normalized = sorted(set(names))
    existing = list(session.scalars(select(Tag).where(Tag.name.in_(normalized))).all()) if normalized else []
    by_name = {tag.name: tag for tag in existing}
    server.tags = [by_name.get(name) or Tag(name=name) for name in normalized]


async def _validate_server_endpoint(host: str, port: int, platform: str, settings) -> ValidatedTarget:
    if platform == "windows":
        return await validate_metrics_endpoint(
            host, port, settings, markers=("windows_",), label="windows_exporter"
        )
    return await validate_exporter(host, port, settings)


def _install_hint(platform: str) -> str:
    return (
        "Install windows_exporter and verify firewall access before registering."
        if platform == "windows"
        else "Install Node Exporter and verify firewall access before registering."
    )


async def _publish_or_503(request: Request, server_id: UUID) -> None:
    try:
        await asyncio.to_thread(
            publish_snapshot,
            request.app.state.session_factory,
            request.app.state.settings.prometheus_discovery_dir,
        )
    except Exception as exc:
        request.app.state.logger.exception("Prometheus discovery publication failed for server %s", server_id)
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Server change was saved, but Prometheus discovery publication is pending retry",
                "server_id": str(server_id),
            },
        ) from exc


def _check_duplicate(session: Session, *, name: str, host: str, port: int, exclude: UUID | None = None) -> None:
    query = select(Server.id).where(or_(Server.name == name, (Server.host == host) & (Server.exporter_port == port)))
    if exclude is not None:
        query = query.where(Server.id != exclude)
    if session.scalar(query) is not None:
        raise HTTPException(status_code=409, detail="A server with this name or exporter endpoint already exists")


@router.get("", response_model=ServerListResponse)
def list_servers(
    user: CurrentUser,
    session: SessionDep,
    environment: Annotated[str | None, Query(pattern="^(production|staging|development)$")] = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ServerListResponse:
    query = select(Server).options(selectinload(Server.tags))
    count_query = select(func.count()).select_from(Server)
    if environment:
        query = query.where(Server.environment == environment)
        count_query = count_query.where(Server.environment == environment)
    if search:
        pattern = f"%{search.strip()}%"
        filter_clause = or_(Server.name.ilike(pattern), Server.host.ilike(pattern), Server.description.ilike(pattern))
        query = query.where(filter_clause)
        count_query = count_query.where(filter_clause)
    items = session.scalars(query.order_by(Server.name).offset(offset).limit(limit)).all()
    return ServerListResponse(items=[_response(item) for item in items], total=session.scalar(count_query) or 0)


@router.post("", response_model=ServerResponse, status_code=status.HTTP_201_CREATED)
async def create_server(
    payload: ServerCreate,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
) -> ServerResponse:
    settings = request.app.state.settings
    if payload.exporter_port not in settings.allowed_ports:
        raise HTTPException(status_code=422, detail=f"Port {payload.exporter_port} is not allowed by monitoring policy")
    _check_duplicate(session, name=payload.name, host=payload.host, port=payload.exporter_port)
    session.commit()
    try:
        target = await _validate_server_endpoint(payload.host, payload.exporter_port, payload.platform, settings)
    except TargetPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ExporterUnreachable as exc:
        raise HTTPException(
            status_code=422,
            detail=f"{exc}. {_install_hint(payload.platform)}",
        ) from exc

    server = Server(
        name=payload.name,
        host=payload.host,
        resolved_address=target.address,
        exporter_port=payload.exporter_port,
        platform=payload.platform,
        environment=payload.environment,
        description=payload.description,
        created_by=user.id,
    )
    try:
        with session.begin():
            _check_duplicate(session, name=payload.name, host=payload.host, port=payload.exporter_port)
            session.add(server)
            _set_tags(session, server, payload.tags)
            session.flush()
            if payload.platform == "windows":
                session.add(
                    ServerIntegration(
                        server_id=server.id,
                        kind="windows_exporter",
                        port=server.exporter_port,
                        metrics_path="/metrics",
                        enabled=True,
                        status="reachable",
                        status_message="windows_exporter responded with expected metrics",
                        last_checked_at=datetime.now(timezone.utc),
                    )
                )
            record_audit(
                session,
                actor_id=user.id,
                action="server.created",
                resource_type="server",
                resource_id=str(server.id),
                details={
                    "name": server.name,
                    "host": server.host,
                    "port": server.exporter_port,
                    "platform": server.platform,
                    "environment": server.environment,
                },
                request_id=request.state.request_id,
            )
            add_registry_outbox(session, "server.created", str(server.id))
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="A server with this name or exporter endpoint already exists") from exc

    await _publish_or_503(request, server.id)
    return _response(server)


@router.get("/{server_id}", response_model=ServerResponse)
def get_server(server_id: UUID, user: CurrentUser, session: SessionDep) -> ServerResponse:
    server = session.scalar(select(Server).options(selectinload(Server.tags)).where(Server.id == server_id))
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    return _response(server)


@router.patch("/{server_id}", response_model=ServerResponse)
async def update_server(
    server_id: UUID,
    payload: ServerUpdate,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
) -> ServerResponse:
    server = session.scalar(select(Server).options(selectinload(Server.tags)).where(Server.id == server_id))
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return _response(server)

    next_host = changes.get("host", server.host)
    next_port = changes.get("exporter_port", server.exporter_port)
    next_name = changes.get("name", server.name)
    _check_duplicate(session, name=next_name, host=next_host, port=next_port, exclude=server.id)
    session.commit()

    endpoint_changed = next_host != server.host or next_port != server.exporter_port
    validated_address = server.resolved_address
    if endpoint_changed:
        if next_port not in request.app.state.settings.allowed_ports:
            raise HTTPException(status_code=422, detail=f"Port {next_port} is not allowed by monitoring policy")
        try:
            target = await _validate_server_endpoint(
                next_host, next_port, server.platform, request.app.state.settings
            )
            validated_address = target.address
        except TargetPolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ExporterUnreachable as exc:
            raise HTTPException(
                status_code=422,
                detail=f"{exc}. {_install_hint(server.platform)} Replace 'registering' with 'updating'.",
            ) from exc

    try:
        with session.begin():
            server = session.scalar(select(Server).options(selectinload(Server.tags)).where(Server.id == server_id))
            if server is None:
                raise HTTPException(status_code=404, detail="Server not found")
            _check_duplicate(session, name=next_name, host=next_host, port=next_port, exclude=server.id)
            previous = {
                "name": server.name,
                "host": server.host,
                "port": server.exporter_port,
                "platform": server.platform,
                "environment": server.environment,
            }
            for field in ("name", "host", "exporter_port", "environment", "description"):
                if field in changes:
                    setattr(server, field, changes[field])
            if endpoint_changed:
                server.resolved_address = validated_address
            if "tags" in changes:
                _set_tags(session, server, changes["tags"])
            record_audit(
                session,
                actor_id=user.id,
                action="server.updated",
                resource_type="server",
                resource_id=str(server.id),
                details={"before": previous, "changed_fields": sorted(changes)},
                request_id=request.state.request_id,
            )
            add_registry_outbox(session, "server.updated", str(server.id))
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="A server with this name or exporter endpoint already exists") from exc

    server = session.scalar(select(Server).options(selectinload(Server.tags)).where(Server.id == server_id))
    await _publish_or_503(request, server_id)
    return _response(server)


@router.delete("/{server_id}", response_model=MessageResponse)
async def delete_server(server_id: UUID, request: Request, user: CurrentUser, session: SessionDep) -> MessageResponse:
    server = session.scalar(select(Server).options(selectinload(Server.tags)).where(Server.id == server_id))
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    name = server.name
    record_audit(
        session,
        actor_id=user.id,
        action="server.deleted",
        resource_type="server",
        resource_id=str(server_id),
        details={"name": name, "host": server.host, "port": server.exporter_port},
        request_id=request.state.request_id,
    )
    add_registry_outbox(session, "server.deleted", str(server_id))
    session.delete(server)
    session.commit()
    await _publish_or_503(request, server_id)
    return MessageResponse(message="Server removed from inventory")
