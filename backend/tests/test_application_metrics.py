import re

from fastapi.testclient import TestClient

from app.core.metrics import refresh_inventory_metrics
from app.models import EndpointMonitor, Server, ServerIntegration


def _value(text: str, series: str) -> float:
    match = re.search(rf"^{re.escape(series)} ([0-9.e+-]+)$", text, re.MULTILINE)
    return float(match.group(1)) if match else 0.0


def test_metrics_endpoint_exposes_application_metrics(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert "infrawatch_build_info" in body
    assert "infrawatch_servers_total" in body
    assert "python_gc_objects_collected_total" in body or "process_resident_memory_bytes" in body


def test_http_requests_are_counted_by_route_template(client: TestClient) -> None:
    series = 'infrawatch_http_requests_total{method="GET",route="/health/live",status="200"}'
    before = _value(client.get("/metrics").text, series)
    client.get("/health/live")
    client.get("/health/live")
    after = _value(client.get("/metrics").text, series)
    assert after - before == 2


def test_inventory_gauges_reflect_database(client: TestClient) -> None:
    application = client.app
    factory = application.state.session_factory
    with factory.begin() as session:
        server = Server(
            name="metrics-test-host",
            host="10.20.30.40",
            resolved_address="10.20.30.40",
            exporter_port=9100,
            platform="linux",
            environment="staging",
        )
        session.add(server)
        session.flush()
        session.add(
            ServerIntegration(
                server_id=server.id,
                kind="cadvisor",
                port=8080,
                metrics_path="/metrics",
                enabled=True,
                status="reachable",
            )
        )
        session.add(
            EndpointMonitor(
                name="metrics-test-endpoint",
                kind="http",
                target="http://10.20.30.40/health",
                expected_status=200,
                environment="staging",
                enabled=True,
            )
        )

    refresh_inventory_metrics(factory, application.state.settings.prometheus_discovery_dir)
    body = client.get("/metrics").text
    assert 'infrawatch_servers_total{environment="staging",platform="linux"} 1.0' in body
    assert 'infrawatch_integrations_total{kind="cadvisor",status="reachable"} 1.0' in body
    assert 'infrawatch_endpoints_total{enabled="true",kind="http"} 1.0' in body
