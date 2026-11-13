"""Static catalog of supported monitoring integrations.

Installation is always performed by the operator on the monitored host. This
module only describes official download locations, required metrics, and the
Prometheus job that consumes each exporter.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class IntegrationDefinition:
    kind: str
    title: str
    category: str
    scope: str
    description: str
    default_port: int | None
    metrics_path: str
    metric_markers: tuple[str, ...]
    job: str
    discovery_file: str
    install_url: str
    requires: str
    dashboard: str
    metrics: tuple[str, ...]
    operating_systems: tuple[str, ...]
    install_steps: tuple[str, ...]

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["metric_markers"] = list(self.metric_markers)
        payload["metrics"] = list(self.metrics)
        payload["operating_systems"] = list(self.operating_systems)
        payload["install_steps"] = list(self.install_steps)
        return payload


SERVER_INTEGRATIONS: tuple[IntegrationDefinition, ...] = (
    IntegrationDefinition(
        kind="cadvisor",
        title="Docker container metrics",
        category="Containers",
        scope="server",
        description=(
            "cAdvisor exposes per-container CPU, memory, filesystem, network and "
            "disk I/O metrics plus configured container limits."
        ),
        default_port=8080,
        metrics_path="/metrics",
        metric_markers=("container_cpu_usage_seconds_total", "container_memory_working_set_bytes"),
        job="cadvisor",
        discovery_file="cadvisor.json",
        install_url="https://github.com/google/cadvisor/releases/latest",
        requires="Docker Engine or containerd on the monitored host",
        dashboard="docker-containers",
        metrics=(
            "Container CPU utilization",
            "Container memory consumption and limits",
            "Container network traffic",
            "Container filesystem usage",
            "Container disk I/O",
            "Container availability and restart count",
        ),
        operating_systems=("Linux",),
        install_steps=(
            "Run cAdvisor as a container on the Docker host you want to inspect.",
            "Publish its metrics port only on a private interface reachable by InfraWatch.",
            "Grant read-only access to the Docker socket and host filesystems it needs.",
            "Verify http://HOST:8080/metrics returns container_ metrics before registering.",
        ),
    ),
    IntegrationDefinition(
        kind="process_exporter",
        title="Process monitoring",
        category="Processes",
        scope="server",
        description=(
            "process-exporter reports CPU, memory and availability for named process "
            "groups, so individual services can be charted and alerted on."
        ),
        default_port=9256,
        metrics_path="/metrics",
        metric_markers=("namedprocess_namegroup_cpu_seconds_total", "namedprocess_namegroup_memory_bytes"),
        job="process",
        discovery_file="process.json",
        install_url="https://github.com/ncabatoff/process-exporter/releases/latest",
        requires="A process-exporter configuration file listing the process groups to watch",
        dashboard="processes",
        metrics=(
            "Per-process-group CPU usage",
            "Per-process-group memory consumption",
            "Process availability",
            "Process thread and I/O counters",
        ),
        operating_systems=("Linux",),
        install_steps=(
            "Download the official process-exporter release and install the binary.",
            "Create /etc/process-exporter/config.yml with the process groups you want to observe.",
            "Start process-exporter with --config.path and --web.listen-address=:9256.",
            "Verify namedprocess_namegroup_ metrics appear at http://HOST:9256/metrics.",
        ),
    ),
    IntegrationDefinition(
        kind="windows_exporter",
        title="Windows server metrics",
        category="Windows",
        scope="server",
        description=(
            "windows_exporter provides CPU, memory, filesystem, network, service and "
            "system metrics for Windows Server and Windows client hosts."
        ),
        default_port=9182,
        metrics_path="/metrics",
        # windows_exporter 0.32 renamed cpu/cs metrics, so validate the family prefix.
        metric_markers=("windows_",),
        job="windows",
        discovery_file="windows.json",
        install_url="https://github.com/prometheus-community/windows_exporter/releases/latest",
        requires="Windows Server 2016 or newer (Windows 10/11 also supported)",
        dashboard="windows-server-details",
        metrics=(
            "CPU utilization per core",
            "Memory utilization",
            "Logical and physical disk capacity",
            "Network interfaces and throughput",
            "Windows service state",
            "System uptime",
        ),
        operating_systems=("Windows",),
        install_steps=(
            "Download the official windows_exporter .msi from the releases page.",
            "Install it with the collectors you need: msiexec /i windows_exporter.msi /qn ENABLED_COLLECTORS=cpu,memory,logical_disk,physical_disk,net,os,service,system,process LISTEN_PORT=9182",
            "The installer registers a Windows service that listens on port 9182.",
            "Allow inbound TCP/9182 only from the InfraWatch monitoring network.",
            "Verify http://WINDOWS-HOST:9182/metrics returns windows_ metrics before registering.",
        ),
    ),
)

PLATFORM_INTEGRATIONS: tuple[IntegrationDefinition, ...] = (
    IntegrationDefinition(
        kind="blackbox",
        title="External endpoint probing",
        category="Availability",
        scope="platform",
        description=(
            "Blackbox Exporter probes HTTP/HTTPS URLs, TCP ports, DNS names and ICMP "
            "hosts, reporting availability, latency and TLS certificate expiry."
        ),
        default_port=9115,
        metrics_path="/probe",
        metric_markers=("probe_success",),
        job="blackbox-http",
        discovery_file="probes-http.json",
        install_url="https://github.com/prometheus/blackbox_exporter/releases/latest",
        requires="Included in this Compose stack; no separate installation required",
        dashboard="network-endpoints",
        metrics=(
            "HTTP and HTTPS availability",
            "API endpoint availability",
            "TCP port reachability",
            "DNS resolution",
            "ICMP ping",
            "TLS certificate expiry",
            "Response time and latency",
        ),
        operating_systems=("Platform",),
        install_steps=(
            "Blackbox Exporter is bundled with InfraWatch and is not published to the host.",
            "Register endpoints in the Endpoints page; discovery is automatic.",
            "ICMP probes run inside the blackbox container with NET_RAW capability.",
        ),
    ),
    IntegrationDefinition(
        kind="loki",
        title="Centralized logging",
        category="Logs",
        scope="platform",
        description=(
            "Grafana Loki stores logs shipped by Grafana Alloy from Linux system logs, "
            "application logs, Docker containers and the bundled Nginx proxy."
        ),
        default_port=3100,
        metrics_path="/ready",
        metric_markers=(),
        job="loki",
        discovery_file="",
        install_url="https://grafana.com/docs/loki/latest/send-data/alloy/",
        requires="Grafana Alloy on each host whose logs you want to collect",
        dashboard="centralized-logs",
        metrics=(
            "System log querying",
            "Application and container logs",
            "Nginx access and error logs",
            "Server identity labels",
            "Severity and time-range filters",
        ),
        operating_systems=("Linux", "Windows"),
        install_steps=(
            "Loki and Grafana Alloy are bundled with InfraWatch and start with Compose.",
            "Alloy collects Docker container logs and /var/log from the InfraWatch host.",
            "For additional hosts, install Alloy with the documented configuration and point it at http://INFRAWATCH-HOST:3100/loki/api/v1/push.",
            "Open Grafana -> Explore -> Loki to query by host, service and severity.",
        ),
    ),
)

ALL_INTEGRATIONS = SERVER_INTEGRATIONS + PLATFORM_INTEGRATIONS
BY_KIND = {definition.kind: definition for definition in ALL_INTEGRATIONS}
SERVER_KINDS = frozenset(definition.kind for definition in SERVER_INTEGRATIONS)
