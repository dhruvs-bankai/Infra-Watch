from functools import lru_cache
import ipaddress

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="../.env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str = Field(min_length=1)
    jwt_secret_key: SecretStr = Field(min_length=32)
    initial_admin_setup_token: SecretStr = Field(min_length=24)
    jwt_issuer: str = "infrawatch"
    jwt_audience: str = "infrawatch-api"
    jwt_access_token_minutes: int = Field(default=30, ge=5, le=1440)
    monitoring_allowed_cidrs: str = (
        "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7"
    )
    monitoring_allowed_ports: str = "9100,8080,9182,9256"
    exporter_connect_timeout_seconds: float = Field(default=3.0, ge=0.2, le=15)
    exporter_response_limit_bytes: int = Field(default=8_388_608, ge=1024)
    prometheus_discovery_dir: str = "/var/lib/infrawatch/file_sd"
    prometheus_url: str = "http://prometheus:9090"
    alertmanager_url: str = "http://alertmanager:9093"
    cors_origins: str = ""

    @field_validator("jwt_secret_key", "initial_admin_setup_token")
    @classmethod
    def validate_secret_minimum(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("secret must contain at least 32 characters")
        return value

    @field_validator("monitoring_allowed_cidrs")
    @classmethod
    def validate_cidrs(cls, value: str) -> str:
        try:
            networks = [ipaddress.ip_network(item.strip(), strict=False) for item in value.split(",") if item.strip()]
        except ValueError as exc:
            raise ValueError("MONITORING_ALLOWED_CIDRS must be comma-separated IPv4/IPv6 CIDRs") from exc
        if not networks:
            raise ValueError("MONITORING_ALLOWED_CIDRS must include at least one CIDR")
        return value

    @field_validator("monitoring_allowed_ports")
    @classmethod
    def validate_ports(cls, value: str) -> str:
        try:
            ports = [int(item.strip()) for item in value.split(",") if item.strip()]
        except ValueError as exc:
            raise ValueError("MONITORING_ALLOWED_PORTS must be comma-separated port numbers") from exc
        if not ports or any(port < 1 or port > 65535 for port in ports):
            raise ValueError("MONITORING_ALLOWED_PORTS must contain ports from 1 through 65535")
        return value

    @property
    def allowed_cidrs(self) -> tuple[str, ...]:
        return tuple(value.strip() for value in self.monitoring_allowed_cidrs.split(",") if value.strip())

    @property
    def allowed_ports(self) -> frozenset[int]:
        try:
            ports = {int(value.strip()) for value in self.monitoring_allowed_ports.split(",") if value.strip()}
        except ValueError as exc:
            raise ValueError("MONITORING_ALLOWED_PORTS must be comma-separated port numbers") from exc
        if not ports or any(port < 1 or port > 65535 for port in ports):
            raise ValueError("MONITORING_ALLOWED_PORTS must contain ports from 1 through 65535")
        return frozenset(ports)

    @property
    def cors_origin_list(self) -> list[str]:
        return [value.strip() for value in self.cors_origins.split(",") if value.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
