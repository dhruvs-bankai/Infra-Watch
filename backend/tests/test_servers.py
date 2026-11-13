import json

import pytest
from fastapi.testclient import TestClient

from app.api import servers as server_routes
from app.models import AuditLog
from app.models import OutboxEvent
from app.services.exporter import ExporterUnreachable, ValidatedTarget


async def fake_validator(host: str, port: int, _settings):
    return ValidatedTarget(host=host, port=port, address="10.20.30.40")


def server_payload(**overrides):
    return {
        "name": "prod-api-01",
        "host": "node-01.internal.example",
        "exporter_port": 9100,
        "environment": "production",
        "description": "Primary API node",
        "tags": ["linux", "web"],
        **overrides,
    }


def test_register_edit_inventory_and_delete_publish_discovery(client: TestClient, auth_headers, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(server_routes, "validate_exporter", fake_validator)
    created = client.post("/api/v1/servers", headers=auth_headers, json=server_payload())
    assert created.status_code == 201, created.text
    server = created.json()
    server_id = server["id"]
    assert server["status"] == "registered"
    assert server["tags"] == ["linux", "web"]

    discovery_file = tmp_path / "file_sd" / "servers.json"
    target = json.loads(discovery_file.read_text(encoding="utf-8"))
    assert target == [
        {
            "targets": ["10.20.30.40:9100"],
            "labels": {
                "instance": "prod-api-01",
                "environment": "production",
                "server_id": server_id,
                "tags": "linux,web",
            },
        }
    ]
    assert not list(discovery_file.parent.glob(".servers-*.tmp"))

    listed = client.get("/api/v1/servers?environment=production&search=prod-api", headers=auth_headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == server_id
    detail = client.get(f"/api/v1/servers/{server_id}", headers=auth_headers)
    assert detail.status_code == 200

    updated = client.patch(
        f"/api/v1/servers/{server_id}",
        headers=auth_headers,
        json={"name": "prod-api-01a", "environment": "staging", "tags": ["linux", "database"]},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "prod-api-01a"
    assert updated.json()["environment"] == "staging"
    changed_target = json.loads(discovery_file.read_text(encoding="utf-8"))[0]
    assert changed_target["labels"]["instance"] == "prod-api-01a"
    assert changed_target["labels"]["environment"] == "staging"
    assert changed_target["labels"]["tags"] == "database,linux"

    deleted = client.delete(f"/api/v1/servers/{server_id}", headers=auth_headers)
    assert deleted.status_code == 200
    assert json.loads(discovery_file.read_text(encoding="utf-8")) == []
    assert client.get(f"/api/v1/servers/{server_id}", headers=auth_headers).status_code == 404


def test_unreachable_exporter_is_not_registered(client: TestClient, auth_headers, monkeypatch):
    async def unreachable(*_args, **_kwargs):
        raise ExporterUnreachable("connection refused")

    monkeypatch.setattr(server_routes, "validate_exporter", unreachable)
    response = client.post("/api/v1/servers", headers=auth_headers, json=server_payload())
    assert response.status_code == 422
    assert "Install Node Exporter" in response.json()["detail"]
    assert client.get("/api/v1/servers", headers=auth_headers).json()["total"] == 0


def test_duplicate_name_and_endpoint_are_rejected(client: TestClient, auth_headers, monkeypatch):
    monkeypatch.setattr(server_routes, "validate_exporter", fake_validator)
    assert client.post("/api/v1/servers", headers=auth_headers, json=server_payload()).status_code == 201
    duplicate_name = client.post(
        "/api/v1/servers", headers=auth_headers,
        json=server_payload(host="node-02.internal.example"),
    )
    assert duplicate_name.status_code == 409
    duplicate_endpoint = client.post(
        "/api/v1/servers", headers=auth_headers,
        json=server_payload(name="another-node"),
    )
    assert duplicate_endpoint.status_code == 409


@pytest.mark.parametrize("host", ["http://10.20.30.40", "127.0.0.1", "169.254.169.254", "bad host"])
def test_unsafe_or_invalid_host_is_rejected(client: TestClient, auth_headers, host: str):
    response = client.post("/api/v1/servers", headers=auth_headers, json=server_payload(host=host))
    assert response.status_code == 422


def test_disallowed_port_and_environment_validation(client: TestClient, auth_headers, monkeypatch):
    monkeypatch.setattr(server_routes, "validate_exporter", fake_validator)
    port_response = client.post("/api/v1/servers", headers=auth_headers, json=server_payload(exporter_port=22))
    assert port_response.status_code == 422
    environment_response = client.post("/api/v1/servers", headers=auth_headers, json=server_payload(environment="production-x"))
    assert environment_response.status_code == 422


def test_update_rejects_explicit_null_values(client: TestClient, auth_headers, monkeypatch):
    monkeypatch.setattr(server_routes, "validate_exporter", fake_validator)
    created = client.post("/api/v1/servers", headers=auth_headers, json=server_payload())
    server_id = created.json()["id"]
    response = client.patch(f"/api/v1/servers/{server_id}", headers=auth_headers, json={"description": None})
    assert response.status_code == 422


def test_audit_records_create_and_delete(client: TestClient, auth_headers, monkeypatch):
    monkeypatch.setattr(server_routes, "validate_exporter", fake_validator)
    created = client.post("/api/v1/servers", headers=auth_headers, json=server_payload())
    server_id = created.json()["id"]
    assert client.delete(f"/api/v1/servers/{server_id}", headers=auth_headers).status_code == 200
    with client.app.state.session_factory() as session:
        entries = session.query(AuditLog).filter(AuditLog.resource_type == "server").all()
        assert [entry.action for entry in entries] == ["server.created", "server.deleted"]


def test_discovery_failure_keeps_committed_change_for_reconciliation(client: TestClient, auth_headers, monkeypatch, tmp_path):
    monkeypatch.setattr(server_routes, "validate_exporter", fake_validator)
    actual_publisher = server_routes.publish_snapshot

    def fail_publisher(*_args, **_kwargs):
        raise OSError("discovery volume temporarily unavailable")

    monkeypatch.setattr(server_routes, "publish_snapshot", fail_publisher)
    response = client.post("/api/v1/servers", headers=auth_headers, json=server_payload())
    assert response.status_code == 503
    server_id = response.json()["detail"]["server_id"]
    assert client.get("/api/v1/servers", headers=auth_headers).json()["total"] == 1

    monkeypatch.setattr(server_routes, "publish_snapshot", actual_publisher)
    actual_publisher(client.app.state.session_factory, str(tmp_path / "file_sd"))
    assert json.loads((tmp_path / "file_sd" / "servers.json").read_text(encoding="utf-8"))[0]["labels"]["server_id"] == server_id
    with client.app.state.session_factory() as session:
        event = session.query(OutboxEvent).filter(OutboxEvent.resource_id == server_id).one()
        assert event.published_at is not None


def test_windows_server_uses_windows_exporter_and_discovery(client: TestClient, auth_headers, monkeypatch, tmp_path) -> None:
    observed: dict[str, object] = {}

    async def windows_validator(host: str, port: int, _settings, **kwargs):
        observed.update(kwargs)
        return ValidatedTarget(host=host, port=port, address="10.20.30.60")

    monkeypatch.setattr(server_routes, "validate_metrics_endpoint", windows_validator)
    created = client.post(
        "/api/v1/servers",
        headers=auth_headers,
        json={"name": "win-dc-01", "host": "10.20.30.60", "platform": "windows", "environment": "production"},
    )
    assert created.status_code == 201, created.text
    server = created.json()
    assert server["platform"] == "windows"
    assert server["exporter_port"] == 9182
    assert observed["markers"] == ("windows_",)

    # Windows hosts are scraped by the windows job, never by the Linux Node Exporter job.
    assert json.loads((tmp_path / "file_sd" / "servers.json").read_text(encoding="utf-8")) == []
    windows_target = json.loads((tmp_path / "file_sd" / "windows.json").read_text(encoding="utf-8"))
    assert windows_target[0]["targets"] == ["10.20.30.60:9182"]
    assert windows_target[0]["labels"]["integration"] == "windows_exporter"

    integrations = client.get(f"/api/v1/servers/{server['id']}/integrations", headers=auth_headers)
    assert integrations.status_code == 200
    assert [item["kind"] for item in integrations.json()] == ["windows_exporter"]
    assert integrations.json()[0]["enabled"] is True


def test_server_id_path_is_validated(client: TestClient, auth_headers):
    response = client.get("/api/v1/servers/not-a-uuid", headers=auth_headers)
    assert response.status_code == 422
