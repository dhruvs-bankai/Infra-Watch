from uuid import uuid4

from app.api import monitoring
from app.models import Server, User
from app.services.alert_history import collect_alert_history


def test_monitoring_apis_require_authentication(client):
    assert client.get("/api/v1/metrics/overview").status_code == 401
    assert client.get("/api/v1/alerts").status_code == 401


def test_overview_marks_metrics_unavailable_when_prometheus_has_no_samples(client, auth_headers, monkeypatch):
    monkeypatch.setattr(monitoring, "_query", lambda _request, _expression: [])
    response = client.get("/api/v1/metrics/overview", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total_servers"] == 0
    assert body["avg_cpu_percent"] is None
    assert body["avg_memory_percent"] is None
    assert body["total_disk_bytes"] is None
    assert body["network_in_bytes_per_second"] is None


def test_non_finite_prometheus_values_are_not_returned_as_metrics():
    rows = [
        {"metric": {"server_id": "one"}, "value": [1, "NaN"]},
        {"metric": {"server_id": "two"}, "value": [1, "+Inf"]},
        {"metric": {"server_id": "three"}, "value": [1, "12.5"]},
    ]
    assert monitoring._values_by_server(rows) == {"three": 12.5}


def test_server_metrics_return_empty_series_instead_of_zero_samples(client, auth_headers, monkeypatch):
    with client.app.state.session_factory.begin() as session:
        user = session.query(User).filter_by(username="admin").one()
        server = Server(
            id=uuid4(),
            name="metrics-empty",
            host="10.20.1.7",
            resolved_address="10.20.1.7",
            exporter_port=9100,
            environment="development",
            description="",
            created_by=user.id,
        )
        session.add(server)
        session.flush()
        server_id = str(server.id)

    monkeypatch.setattr(monitoring, "_prometheus", lambda *_args, **_kwargs: {"result": []})
    response = client.get(f"/api/v1/servers/{server_id}/metrics?metric=cpu&hours=24", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["points"] == []
    assert response.json()["unit"] == "percent"


def test_alertmanager_events_are_persisted_and_resolutions_are_kept(client, auth_headers, monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [{
                "fingerprint": "abc123",
                "labels": {"alertname": "NodeCPUUsageHigh", "severity": "warning", "instance": "db-01"},
                "status": {"state": "active"},
                "annotations": {"summary": "CPU is high"},
                "startsAt": "2026-11-12T00:00:00Z",
                "endsAt": "2026-11-12T00:05:00Z",
            }]

    monkeypatch.setattr("app.services.alert_history.httpx.get", lambda *_args, **_kwargs: Response())
    assert collect_alert_history(client.app.state.session_factory, "http://alertmanager:9093") == 1
    response = client.get("/api/v1/alerts", headers=auth_headers)
    assert response.status_code == 200
    alert = response.json()["items"][0]
    assert alert["name"] == "NodeCPUUsageHigh"
    assert alert["instance"] == "db-01"
    assert alert["summary"] == "CPU is high"
    assert alert["status"] == "active"
    assert response.json()["active_count"] == 1

    class EmptyResponse(Response):
        def json(self):
            return []

    monkeypatch.setattr("app.services.alert_history.httpx.get", lambda *_args, **_kwargs: EmptyResponse())
    assert collect_alert_history(client.app.state.session_factory, "http://alertmanager:9093") == 0
    history = client.get("/api/v1/alerts", headers=auth_headers).json()
    assert history["active_count"] == 0
    assert history["items"][0]["status"] == "resolved"
    assert history["items"][0]["ends_at"]
