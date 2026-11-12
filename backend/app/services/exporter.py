import asyncio
import ipaddress
import socket
from dataclasses import dataclass

import httpx

from app.core.config import Settings


class TargetPolicyError(ValueError):
    pass


class ExporterUnreachable(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedTarget:
    host: str
    port: int
    address: str


_DENIED_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "168.63.129.16/32",  # Azure platform virtual IP
        "100.100.100.200/32",  # Alibaba Cloud metadata
        "fd00:ec2::254/128",  # AWS IPv6 metadata
    )
)


def _approved_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address, settings: Settings) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified or address.is_reserved:
        return False
    if any(address.version == network.version and address in network for network in _DENIED_NETWORKS):
        return False
    try:
        allowed = [ipaddress.ip_network(value, strict=False) for value in settings.allowed_cidrs]
    except ValueError as exc:
        raise TargetPolicyError("Monitoring network policy contains an invalid CIDR") from exc
    return any(address.version == network.version and address in network for network in allowed)


async def _resolve(host: str, port: int, timeout: float) -> list[str]:
    try:
        records = await asyncio.wait_for(
            asyncio.to_thread(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM),
            timeout=timeout,
        )
    except (OSError, asyncio.TimeoutError) as exc:
        raise ExporterUnreachable("The hostname could not be resolved") from exc
    addresses = sorted({record[4][0] for record in records})
    if not addresses:
        raise ExporterUnreachable("The hostname has no address records")
    return addresses


def _url_host(address: str) -> str:
    parsed = ipaddress.ip_address(address)
    return f"[{parsed.compressed}]" if parsed.version == 6 else parsed.compressed


async def validate_exporter(host: str, port: int, settings: Settings) -> ValidatedTarget:
    if port not in settings.allowed_ports:
        raise TargetPolicyError(f"Port {port} is not in MONITORING_ALLOWED_PORTS")

    try:
        literal = ipaddress.ip_address(host)
        addresses = [literal.compressed]
    except ValueError:
        addresses = await _resolve(host, port, settings.exporter_connect_timeout_seconds)

    parsed = [ipaddress.ip_address(address) for address in addresses]
    # Reject mixed DNS answers as well: a future resolver choice must not escape the policy.
    if any(not _approved_address(address, settings) for address in parsed):
        raise TargetPolicyError("The target resolves outside the approved monitoring networks")

    failures: list[str] = []
    timeout = httpx.Timeout(settings.exporter_connect_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
        for address in addresses:
            target_url = f"http://{_url_host(address)}:{port}/metrics"
            authority_host = f"[{host}]" if ":" in host else host
            authority = f"{authority_host}:{port}"
            try:
                async with client.stream("GET", target_url, headers={"Host": authority, "Accept": "text/plain, application/openmetrics-text"}) as response:
                    if response.status_code != 200:
                        failures.append(f"HTTP {response.status_code}")
                        continue
                    content_type = response.headers.get("content-type", "").lower()
                    if "text/plain" not in content_type and "openmetrics-text" not in content_type:
                        failures.append("endpoint did not return a Prometheus text metrics content type")
                        continue
                    total = 0
                    has_node_metric = False
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > settings.exporter_response_limit_bytes:
                            failures.append("metrics response exceeded the configured size limit")
                            break
                        if b"node_" in chunk:
                            has_node_metric = True
                    else:
                        if has_node_metric:
                            return ValidatedTarget(host=host, port=port, address=address)
                        failures.append("endpoint did not expose Node Exporter metrics")
            except httpx.HTTPError:
                failures.append("connection failed")

    reason = failures[0] if failures else "connection failed"
    raise ExporterUnreachable(f"Could not validate Node Exporter at {host}:{port}: {reason}")
