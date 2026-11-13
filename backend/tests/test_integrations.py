import json

import pytest
from fastapi.testclient import TestClient

from app.api import integrations as integration_routes
from app.models import AuditLog, Server
from app.services.exporter import ExporterUnreachable, ValidatedTarget

INTEGRATION_PORT = 19100


async def fake_metrics_endpoint(host, port, _settings, **_kwargs):
    return ValidatedTarget(host=host, port=port, address="10.20.30.40")


async def fake_probe_target(*_args, **_kwargs):
    return "203.0.113.10"


@pytest.fixture
def server(client: TestClient, auth_headers, monkeypatch):
    async def validator(host, port, _settings):
        return ValidatedTarget(host=host, port=port, address="10.20.30.40")

    monkeypatch.setattr("app.api.servers.validate_exporter", validator)
    response = client.post(
        "/api/v1/servers",
        headers=auth_headers,
        json={
            "name": "docker-host-01",
            "host": "10.20.30.40",
            "exporter_port": 9100,
            "environment": "production",
            "description": "Container host",
            "tags": ["docker"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_integration_catalog_and_instances_require_authentication(client):
    assert client.get("/api/v1/integrations").status_code == 401
    assert client.get("/api/v1/endpoints").status_code == 401
    assert client.post("/api/v1/endpoints", json={}).status_code == 401


def test_catalog_lists_supported_integrations(client, auth_headers):
    response = client.get("/api/v1/integrations", headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    kinds = {item["kind"] for item in body["items"]}
    assert {"cadvisor", "process_exporter", "windows_exporter", "blackbox", "loki"} <= kinds
    windows = next(item for item in body["items"] if item["kind"] == "windows_exporter")
    assert windows["default_port"] == 9182
    assert windows["install_url"].startswith("https://github.com/prometheus-community/windows_exporter")
    assert any("msiexec" in step for step in windows["install_steps"])
    cadvisor = next(item for item in body["items"] if item["kind"] == "cadvisor")
    assert cadvisor["scope"] == "server"
    assert cadvisor["configured_count"] == 0
    assert body["instances"] == []


def test_attach_verify_disable_and_remove_cadvisor(client, auth_headers, server, monkeypatch, tmp_path):
    monkeypatch.setattr(integration_routes, "validate_metrics_endpoint", fake_metrics_endpoint)
    created = client.post(
        f"/api/v1/servers/{server['id']}/integrations",
        headers=auth_headers,
        json={"kind": "cadvisor", "port": INTEGRATION_PORT, "metrics_path": "/metrics"},
    )
    assert created.status_code == 201, created.text
    integration = created.json()
    assert integration["status"] == "reachable"
    assert integration["server_name"] == "docker-host-01"

    snapshot = json.loads((tmp_path / "file_sd" / "cadvisor.json").read_text(encoding="utf-8"))
    assert snapshot == [
        {
            "targets": [f"10.20.30.40:{INTEGRATION_PORT}"],
            "labels": {
                "instance": "docker-host-01",
                "server_id": server["id"],
                "environment": "production",
                "integration": "cadvisor",
                "metrics_path": "/metrics",
                "tags": "docker",
                "category": "Containers",
            },
        }
    ]
    # The Node Exporter snapshot is preserved and separate from integration targets.
    node_snapshot = json.loads((tmp_path / "file_sd" / "servers.json").read_text(encoding="utf-8"))
    assert node_snapshot[0]["targets"] == ["10.20.30.40:9100"]
    assert len(json.loads((tmp_path / "file_sd" / "process.json").read_text(encoding="utf-8"))) == 0

    duplicate = client.post(
        f"/api/v1/servers/{server['id']}/integrations",
        headers=auth_headers,
        json={"kind": "cadvisor", "port": INTEGRATION_PORT},
    )
    assert duplicate.status_code == 409

    checked = client.post(f"/api/v1/integrations/{integration['id']}/check", headers=auth_headers)
    assert checked.status_code == 200
    assert checked.json()["status"] == "reachable"

    disabled = client.patch(
        f"/api/v1/integrations/{integration['id']}", headers=auth_headers, json={"enabled": False}
    )
    assert disabled.status_code == 200
    assert json.loads((tmp_path / "file_sd" / "cadvisor.json").read_text(encoding="utf-8")) == []

    removed = client.delete(f"/api/v1/integrations/{integration['id']}", headers=auth_headers)
    assert removed.status_code == 200
    assert client.get(f"/api/v1/servers/{server['id']}/integrations", headers=auth_headers).json() == []


def test_unreachable_or_disallowed_exporter_is_not_stored(client, auth_headers, server, monkeypatch):
    async def unreachable(*_args, **_kwargs):
        raise ExporterUnreachable("connection refused")

    monkeypatch.setattr(integration_routes, "validate_metrics_endpoint", unreachable)
    response = client.post(
        f"/api/v1/servers/{server['id']}/integrations",
        headers=auth_headers,
        json={"kind": "process_exporter", "port": INTEGRATION_PORT},
    )
    assert response.status_code == 422
    assert "process-exporter" in response.json()["detail"] or "Connection" in response.json()["detail"]
    assert client.get(f"/api/v1/servers/{server['id']}/integrations", headers=auth_headers).json() == []

    disallowed = client.post(
        f"/api/v1/servers/{server['id']}/integrations",
        headers=auth_headers,
        json={"kind": "windows_exporter", "port": 22},
    )
    assert disallowed.status_code == 422
    assert "MONITORING_ALLOWED_PORTS" in disallowed.json()["detail"]


def test_unsupported_integration_kind_is_rejected(client, auth_headers, server):
    response = client.post(
        f"/api/v1/servers/{server['id']}/integrations",
        headers=auth_headers,
        json={"kind": "ftp_exporter", "port": INTEGRATION_PORT},
    )
    assert response.status_code == 422


def test_endpoint_lifecycle_writes_probe_discovery(client, auth_headers, monkeypatch, tmp_path):
    monkeypatch.setattr(integration_routes, "validate_probe_target", fake_probe_target)
    created = client.post(
        "/api/v1/endpoints",
        headers=auth_headers,
        json={
            "name": "Public API",
            "kind": "https",
            "target": "https://api.example.com/health?deep=true",
            "environment": "production",
            "description": "Customer API",
            "interval_seconds": 30,
        },
    )
    assert created.status_code == 201, created.text
    monitor = created.json()
    assert monitor["port"] == 443
    assert monitor["probe_status"] == "unknown"
    assert monitor["ssl_expires_at"] is None

    snapshot = json.loads((tmp_path / "file_sd" / "probes-http.json").read_text(encoding="utf-8"))
    assert snapshot[0]["targets"] == ["https://api.example.com/health?deep=true"]
    labels = snapshot[0]["labels"]
    assert labels["instance"] == "Public API"
    assert labels["probe_kind"] == "https"
    assert labels["endpoint_id"] == monitor["id"]
    assert labels["job"] == "blackbox-http"

    duplicate = client.post(
        "/api/v1/endpoints",
        headers=auth_headers,
        json={"name": "Duplicate API", "kind": "https", "target": "https://api.example.com/health?deep=true"},
    )
    assert duplicate.status_code == 409

    listed = client.get("/api/v1/endpoints?kind=https&search=Public", headers=auth_headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["healthy"] == 0

    updated = client.patch(
        f"/api/v1/endpoints/{monitor['id']}",
        headers=auth_headers,
        json={"name": "Public API (primary)", "description": "Primary health endpoint"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Public API (primary)"

    removed = client.delete(f"/api/v1/endpoints/{monitor['id']}", headers=auth_headers)
    assert removed.status_code == 200
    assert json.loads((tmp_path / "file_sd" / "probes-http.json").read_text(encoding="utf-8")) == []


def test_tcp_dns_and_icmp_targets_are_normalized(client, auth_headers, monkeypatch, tmp_path):
    monkeypatch.setattr(integration_routes, "validate_probe_target", fake_probe_target)
    tcp = client.post(
        "/api/v1/endpoints",
        headers=auth_headers,
        json={"name": "Postgres primary", "kind": "tcp", "target": "db.example.com", "port": 5432},
    )
    assert tcp.status_code == 201, tcp.text
    assert tcp.json()["target"] == "db.example.com"
    assert tcp.json()["port"] == 5432

    dns = client.post(
        "/api/v1/endpoints",
        headers=auth_headers,
        json={"name": "Zone resolution", "kind": "dns", "target": "example.com"},
    )
    assert dns.status_code == 201, dns.text

    icmp = client.post(
        "/api/v1/endpoints",
        headers=auth_headers,
        json={"name": "Gateway ping", "kind": "icmp", "target": "10.20.30.1"},
    )
    assert icmp.status_code == 201, icmp.text

    ipv6_tcp = client.post(
        "/api/v1/endpoints",
        headers=auth_headers,
        json={"name": "IPv6 database", "kind": "tcp", "target": "fd00::15", "port": 5432},
    )
    assert ipv6_tcp.status_code == 201, ipv6_tcp.text
    assert ipv6_tcp.json()["target"] == "fd00::15"

    missing_port = client.post(
        "/api/v1/endpoints",
        headers=auth_headers,
        json={"name": "No port tcp", "kind": "tcp", "target": "db.example.com"},
    )
    assert missing_port.status_code == 422

    dns_with_port = client.post(
        "/api/v1/endpoints",
        headers=auth_headers,
        json={"name": "DNS with port", "kind": "dns", "target": "example.com", "port": 53},
    )
    assert dns_with_port.status_code == 422

    tcp_snapshot = json.loads((tmp_path / "file_sd" / "probes-tcp.json").read_text(encoding="utf-8"))
    dns_snapshot = json.loads((tmp_path / "file_sd" / "probes-dns.json").read_text(encoding="utf-8"))
    icmp_snapshot = json.loads((tmp_path / "file_sd" / "probes-icmp.json").read_text(encoding="utf-8"))
    assert {entry["targets"][0] for entry in tcp_snapshot} == {"db.example.com:5432", "[fd00::15]:5432"}
    assert all(entry["labels"]["job"] == "blackbox-tcp" for entry in tcp_snapshot)
    assert dns_snapshot[0]["targets"] == ["example.com"]
    assert dns_snapshot[0]["labels"]["job"] == "blackbox-dns"
    assert icmp_snapshot[0]["targets"] == ["10.20.30.1"]
    assert icmp_snapshot[0]["labels"]["job"] == "blackbox-icmp"


@pytest.mark.parametrize(
    "target",
    [
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost:8000/",
        "ftp://example.com/file",
        "https://user:secret@example.com/",
    ],
)
def test_unsafe_probe_targets_are_rejected(client, auth_headers, target):
    response = client.post(
        "/api/v1/endpoints",
        headers=auth_headers,
        json={"name": "Unsafe probe", "kind": "http", "target": target},
    )
    assert response.status_code == 422, response.text


def test_endpoint_server_association_and_missing_server(client, auth_headers, server, monkeypatch):
    monkeypatch.setattr(integration_routes, "validate_probe_target", fake_probe_target)
    response = client.post(
        "/api/v1/endpoints",
        headers=auth_headers,
        json={
            "name": "Hosted API",
            "kind": "http",
            "target": "http://api.example.com/health",
            "server_id": server["id"],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["server_name"] == "docker-host-01"

    missing = client.post(
        "/api/v1/endpoints",
        headers=auth_headers,
        json={
            "name": "Orphan API",
            "kind": "http",
            "target": "http://other.example.com/",
            "server_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
        },
    )
    assert missing.status_code == 422


def test_integration_and_endpoint_changes_are_audited(client, auth_headers, server, monkeypatch):
    monkeypatch.setattr(integration_routes, "validate_metrics_endpoint", fake_metrics_endpoint)
    monkeypatch.setattr(integration_routes, "validate_probe_target", fake_probe_target)
    created = client.post(
        f"/api/v1/servers/{server['id']}/integrations",
        headers=auth_headers,
        json={"kind": "cadvisor", "port": INTEGRATION_PORT},
    )
    assert created.status_code == 201
    client.delete(f"/api/v1/integrations/{created.json()['id']}", headers=auth_headers)
    endpoint = client.post(
        "/api/v1/endpoints",
        headers=auth_headers,
        json={"name": "Audited endpoint", "kind": "icmp", "target": "10.20.30.1"},
    )
    assert endpoint.status_code == 201
    with client.app.state.session_factory() as session:
        actions = [
            entry.action
            for entry in session.query(AuditLog).order_by(AuditLog.created_at).all()
            if entry.resource_type in {"monitoring_integration", "endpoint_monitor"}
        ]
    assert actions == ["integration.created", "integration.deleted", "endpoint.created"]


def test_multiple_exporter_jobs_do_not_collide(client, auth_headers, server, monkeypatch, tmp_path):
    monkeypatch.setattr(integration_routes, "validate_metrics_endpoint", fake_metrics_endpoint)
    monkeypatch.setattr(integration_routes, "validate_probe_target", fake_probe_target)
    for kind, port in (("cadvisor", INTEGRATION_PORT), ("process_exporter", INTEGRATION_PORT)):
        response = client.post(
            f"/api/v1/servers/{server['id']}/integrations",
            headers=auth_headers,
            json={"kind": kind, "port": port},
        )
        assert response.status_code == 201, response.text
    assert len(json.loads((tmp_path / "file_sd" / "cadvisor.json").read_text(encoding="utf-8"))) == 1
    assert len(json.loads((tmp_path / "file_sd" / "process.json").read_text(encoding="utf-8"))) == 1

    # Deleting the server cascades its integrations and clears their targets only.
    assert client.delete(f"/api/v1/servers/{server['id']}", headers=auth_headers).status_code == 200
    assert json.loads((tmp_path / "file_sd" / "cadvisor.json").read_text(encoding="utf-8")) == []
    assert json.loads((tmp_path / "file_sd" / "process.json").read_text(encoding="utf-8")) == []

    with client.app.state.session_factory() as session:
        assert session.query(Server).count() == 0
