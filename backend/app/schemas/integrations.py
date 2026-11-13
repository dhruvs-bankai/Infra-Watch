import ipaddress
import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.servers import Environment, normalize_host

ServerIntegrationKind = Literal["cadvisor", "process_exporter", "windows_exporter"]
EndpointKind = Literal["http", "https", "tcp", "dns", "icmp"]

_PATH = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]{0,190}$")
_ENDPOINT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._():/-]{1,99}$")


def normalize_metrics_path(value: str) -> str:
    value = value.strip()
    if not value.startswith("/") or not _PATH.fullmatch(value) or ".." in value or "//" in value:
        raise ValueError("Metrics path must be a single absolute path such as /metrics")
    return value


def normalize_endpoint_target(kind: str, raw: str) -> tuple[str, int | None]:
    """Canonicalize a probe target. Only http/https accept a URL."""

    value = raw.strip()
    if kind in {"http", "https"}:
        if len(value) > 2048:
            raise ValueError("URL is too long")
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"}:
            raise ValueError("URL must start with http:// or https://")
        if parts.username or parts.password:
            raise ValueError("URLs with embedded credentials are not supported")
        if parts.fragment:
            raise ValueError("URL fragments are not allowed")
        if not parts.hostname:
            raise ValueError("URL must include a hostname")
        host = normalize_host(parts.hostname)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        path = parts.path or "/"
        if not _PATH.fullmatch(path) or ".." in path:
            raise ValueError("URL path is not allowed")
        query = f"?{parts.query}" if parts.query else ""
        netloc = f"[{host}]" if ":" in host else host
        if port not in {80, 443}:
            netloc = f"{netloc}:{port}"
        return urlunsplit((parts.scheme, netloc, path, query.lstrip("?"), "")), port
    if kind == "tcp":
        host = normalize_host(value)
        return host, None
    host = normalize_host(value)
    return host, None


class ServerIntegrationCreate(BaseModel):
    kind: ServerIntegrationKind
    port: int = Field(ge=1, le=65535)
    metrics_path: str = "/metrics"
    enabled: bool = True

    @field_validator("metrics_path")
    @classmethod
    def clean_path(cls, value: str) -> str:
        return normalize_metrics_path(value)


class ServerIntegrationUpdate(BaseModel):
    port: int | None = Field(default=None, ge=1, le=65535)
    metrics_path: str | None = None
    enabled: bool | None = None

    @field_validator("metrics_path")
    @classmethod
    def clean_path(cls, value: str | None) -> str | None:
        return normalize_metrics_path(value) if value is not None else None

    @model_validator(mode="after")
    def reject_explicit_nulls(self):
        for field_name in self.model_fields_set:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        return self


class ServerIntegrationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    server_id: UUID
    server_name: str = ""
    kind: str
    title: str = ""
    port: int
    metrics_path: str
    enabled: bool
    status: str
    status_message: str
    scrape_status: str = "unknown"
    last_checked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class IntegrationCatalogItem(BaseModel):
    kind: str
    title: str
    category: str
    scope: str
    description: str
    default_port: int | None
    metrics_path: str
    job: str
    install_url: str
    requires: str
    dashboard: str
    metrics: list[str]
    operating_systems: list[str]
    install_steps: list[str]
    configured_count: int = 0
    reachable_count: int = 0


class IntegrationCatalogResponse(BaseModel):
    items: list[IntegrationCatalogItem]
    instances: list[ServerIntegrationResponse]
    endpoint_count: int


class EndpointMonitorCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    kind: EndpointKind
    target: str = Field(min_length=1, max_length=2048)
    port: int | None = Field(default=None, ge=1, le=65535)
    path: str = "/"
    expected_status: int = Field(default=200, ge=200, le=299)
    interval_seconds: int = Field(default=30, ge=10, le=300)
    timeout_seconds: int = Field(default=5, ge=1, le=60)
    environment: Environment = "production"
    description: str = Field(default="", max_length=1000)
    server_id: UUID | None = None
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not _ENDPOINT_NAME.fullmatch(value):
            raise ValueError("Endpoint name may contain letters, numbers, spaces and . _ : / -")
        return value

    @field_validator("target")
    @classmethod
    def clean_target(cls, value: str) -> str:
        if any(character in value for character in "\r\n\t"):
            raise ValueError("Target must be a single line")
        return value.strip()

    @model_validator(mode="after")
    def normalize(self):
        target, default_port = normalize_endpoint_target(self.kind, self.target)
        if self.kind == "tcp":
            if self.port is None:
                raise ValueError("TCP checks require a port")
            if not is_ip_literal(self.target) and ":" in self.target:
                raise ValueError("Enter the TCP host and port in separate fields")
        if self.kind in {"dns", "icmp"} and self.port is not None:
            raise ValueError(f"{self.kind.upper()} checks do not accept a port")
        if self.kind in {"http", "https"}:
            if self.port is not None and self.port != default_port:
                raise ValueError("Change the port inside the URL instead of the port field")
            if not _PATH.fullmatch(self.path) or ".." in self.path:
                raise ValueError("Path must be a single absolute path")
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "port", self.port if self.kind == "tcp" else default_port)
        return self


class EndpointMonitorUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    target: str | None = Field(default=None, min_length=1, max_length=2048)
    port: int | None = Field(default=None, ge=1, le=65535)
    path: str | None = None
    expected_status: int | None = Field(default=None, ge=200, le=299)
    interval_seconds: int | None = Field(default=None, ge=10, le=300)
    timeout_seconds: int | None = Field(default=None, ge=1, le=60)
    environment: Environment | None = None
    description: str | None = Field(default=None, max_length=1000)
    server_id: UUID | None = None
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.split())
        if not _ENDPOINT_NAME.fullmatch(value):
            raise ValueError("Endpoint name may contain letters, numbers, spaces and . _ : / -")
        return value

    @field_validator("target")
    @classmethod
    def clean_target(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if any(character in value for character in "\r\n\t"):
            raise ValueError("Target must be a single line")
        return value.strip()


class EndpointMonitorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    kind: str
    target: str
    port: int | None
    path: str
    expected_status: int
    interval_seconds: int
    timeout_seconds: int
    environment: str
    description: str
    server_id: UUID | None
    server_name: str = ""
    enabled: bool
    probe_status: str = "unknown"
    latency_seconds: float | None = None
    ssl_expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class EndpointListResponse(BaseModel):
    items: list[EndpointMonitorResponse]
    total: int
    healthy: int
    failing: int


def is_ip_literal(value: str) -> bool:
    try:
        ipaddress.ip_address(value.strip("[]"))
        return True
    except ValueError:
        return False
