import httpx
import pytest

from app.services import exporter
from app.services.exporter import ExporterUnreachable, TargetPolicyError


@pytest.mark.parametrize("host", ["127.0.0.1", "169.254.169.254", "8.8.8.8"])
async def test_forbidden_or_unapproved_addresses_are_rejected(client, host: str):
    settings = client.app.state.settings
    with pytest.raises(TargetPolicyError):
        await exporter.validate_exporter(host, 9100, settings)


async def test_dns_mixed_answer_is_rejected_before_connection(client, monkeypatch):
    async def resolve(*_args, **_kwargs):
        return ["10.20.30.40", "8.8.8.8"]

    monkeypatch.setattr(exporter, "_resolve", resolve)
    with pytest.raises(TargetPolicyError):
        await exporter.validate_exporter("node.internal.example", 9100, client.app.state.settings)


async def test_registration_connects_to_pinned_ip_and_requires_node_metrics(client, monkeypatch):
    async def resolve(*_args, **_kwargs):
        return ["10.20.30.40"]

    monkeypatch.setattr(exporter, "_resolve", resolve)
    original_client = httpx.AsyncClient
    observed: dict[str, str] = {}

    def client_factory(**kwargs):
        def respond(request: httpx.Request) -> httpx.Response:
            observed["url"] = str(request.url)
            observed["host"] = request.headers["host"]
            return httpx.Response(
                200,
                headers={"content-type": "text/plain; version=0.0.4"},
                content=b"# HELP node_cpu_seconds_total CPU seconds\nnode_cpu_seconds_total{mode=\"idle\"} 10\n",
            )

        kwargs["transport"] = httpx.MockTransport(respond)
        return original_client(**kwargs)

    monkeypatch.setattr(exporter.httpx, "AsyncClient", client_factory)
    result = await exporter.validate_exporter("node.internal.example", 9100, client.app.state.settings)
    assert result.address == "10.20.30.40"
    assert observed["url"] == "http://10.20.30.40:9100/metrics"
    assert observed["host"] == "node.internal.example:9100"


async def test_wrong_endpoint_response_is_rejected(client, monkeypatch):
    async def resolve(*_args, **_kwargs):
        return ["10.20.30.40"]

    monkeypatch.setattr(exporter, "_resolve", resolve)
    original_client = httpx.AsyncClient

    def client_factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(
            lambda _request: httpx.Response(200, headers={"content-type": "text/plain"}, content=b"hello world")
        )
        return original_client(**kwargs)

    monkeypatch.setattr(exporter.httpx, "AsyncClient", client_factory)
    with pytest.raises(ExporterUnreachable, match="did not expose Node Exporter metrics"):
        await exporter.validate_exporter("node.internal.example", 9100, client.app.state.settings)
