import ipaddress
import re
from typing import Literal
from uuid import UUID

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Environment = Literal["production", "staging", "development"]
Platform = Literal["linux", "windows"]
_TAG = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,39}$")


def normalize_host(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 253 or any(char in value for char in "/\\@?#%\r\n\t "):
        raise ValueError("Enter a hostname or IP address without a URL scheme or path")
    try:
        return ipaddress.ip_address(value.strip("[]")).compressed
    except ValueError:
        pass
    if value.startswith("[") or value.endswith("]") or value.endswith("."):
        value = value.rstrip(".")
    try:
        ascii_host = value.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("Hostname is invalid") from exc
    if len(ascii_host) > 253 or ascii_host in {"localhost"} or ascii_host.endswith(".localhost"):
        raise ValueError("Localhost names are not allowed")
    labels = ascii_host.split(".")
    if any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or not re.fullmatch(r"[a-z0-9-]+", label)
        for label in labels
    ):
        raise ValueError("Hostname is invalid")
    return ascii_host


class ServerCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    host: str = Field(min_length=1, max_length=253)
    exporter_port: int = Field(default=9100, ge=1, le=65535)
    platform: Platform = "linux"
    environment: Environment = "production"
    description: str = Field(default="", max_length=1000)
    tags: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if len(value) < 2:
            raise ValueError("Server name must contain at least 2 characters")
        return value

    @field_validator("host")
    @classmethod
    def clean_host(cls, value: str) -> str:
        return normalize_host(value)

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def default_windows_port(self):
        if self.platform == "windows" and "exporter_port" not in self.model_fields_set:
            object.__setattr__(self, "exporter_port", 9182)
        return self

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for raw in values:
            tag = raw.strip().lower()
            if not _TAG.fullmatch(tag):
                raise ValueError("Tags may contain letters, numbers, dots, underscores, and hyphens")
            if tag not in result:
                result.append(tag)
        return result


class ServerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    host: str | None = Field(default=None, min_length=1, max_length=253)
    exporter_port: int | None = Field(default=None, ge=1, le=65535)
    environment: Environment | None = None
    description: str | None = Field(default=None, max_length=1000)
    tags: list[str] | None = Field(default=None, max_length=20)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        if len(cleaned) < 2:
            raise ValueError("Server name must contain at least 2 characters")
        return cleaned

    @field_validator("host")
    @classmethod
    def clean_host(cls, value: str | None) -> str | None:
        return normalize_host(value) if value is not None else None

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, values: list[str] | None) -> list[str] | None:
        return ServerCreate.clean_tags(values) if values is not None else None

    @model_validator(mode="after")
    def reject_explicit_nulls(self):
        for field_name in self.model_fields_set:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        return self


class ServerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    host: str
    exporter_port: int
    platform: str
    environment: str
    description: str
    tags: list[str]
    status: Literal["registered"] = "registered"
    created_at: datetime
    updated_at: datetime


class ServerListResponse(BaseModel):
    items: list[ServerResponse]
    total: int
