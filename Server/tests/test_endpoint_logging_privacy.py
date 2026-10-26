"""Operator URL credentials survive requests, never application or native diagnostics."""

import importlib
import io
import logging
import queue
from types import SimpleNamespace
import urllib.error
import urllib.request

import httpx
import pytest

from core.config import config
from core.endpoint_logging import endpoint_for_log
from core.telemetry import RecordType, TelemetryCollector, TelemetryConfig, TelemetryRecord
from services.api_key_service import ApiKeyService


QUERY = "query-canary-51c3"
USER = "userinfo-canary-71a4"
FRAGMENT = "fragment-canary-84b2"
KEY = "api-key-canary-c911"
ENDPOINT = f"https://collector.example:8443/events?arbitrary={QUERY}"
UNTRUSTED = f"https://{USER}:password@collector.example/events?unknown={QUERY}#{FRAGMENT}"


@pytest.fixture
def diagnostics(tmp_path, monkeypatch):
    """Exercise both operational sinks, including formatted exception records."""
    stream = io.StringIO()
    monkeypatch.setenv("UNITY_MCP_LOG_DIR", str(tmp_path))
    stderr = logging.StreamHandler(stream)
    log_path = tmp_path / "server.log"
    disk = logging.FileHandler(log_path, encoding="utf-8")
    loggers = [
        logging.getLogger(name) for name in ("mcp-for-unity-server", "unity-mcp-telemetry", "httpx")
    ]
    previous = [(item.level, item.propagate) for item in loggers]
    for item in loggers:
        item.setLevel(logging.DEBUG)
        item.propagate = False
        item.addHandler(stderr)
        item.addHandler(disk)
    try:
        yield stream
    finally:
        for item, (level, propagate) in zip(loggers, previous):
            item.removeHandler(stderr)
            item.removeHandler(disk)
            item.setLevel(level)
            item.propagate = propagate
        disk.close()
        for output in (stream.getvalue(), log_path.read_text(encoding="utf-8")):
            for secret in (QUERY, USER, FRAGMENT, KEY):
                assert secret not in output


@pytest.fixture
def telemetry_config(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "telemetry_endpoint", ENDPOINT)
    monkeypatch.setenv("UNITY_MCP_DISABLE_TELEMETRY", "true")
    monkeypatch.delenv("UNITY_MCP_TELEMETRY_ENDPOINT", raising=False)
    monkeypatch.setattr(TelemetryConfig, "_get_data_directory", lambda self: tmp_path)
    return TelemetryConfig


def record():
    return TelemetryRecord(RecordType.USAGE, 1.0, "customer", "session", {})


@pytest.mark.parametrize(
    "endpoint, expected",
    [
        (UNTRUSTED, "https://collector.example/events"),
        (ENDPOINT, "https://collector.example:8443/events"),
        (f"https://[{USER}/?{QUERY}#{FRAGMENT}", "<invalid endpoint>"),
        (f"https://[2001:db8::1]:8443/events?unknown={QUERY}", "https://[2001:db8::1]:8443/events"),
    ],
)
def test_endpoint_rendering_uses_components_not_parameter_names(endpoint, expected):
    assert endpoint_for_log(endpoint) == expected


@pytest.mark.parametrize(
    "candidate",
    [
        UNTRUSTED,
        f"https://collector.example/events#{FRAGMENT}",
        f"https://collector.example:{USER}/?unknown={QUERY}",
        f"https://[{USER}/?unknown={QUERY}",
        f"{USER}://collector.example/?unknown={QUERY}",
        f"https://collector.example/\n?unknown={QUERY}",
    ],
)
def test_invalid_telemetry_url_never_logs_input_or_parser_exception(
    candidate, diagnostics, telemetry_config, monkeypatch
):
    monkeypatch.setenv("UNITY_MCP_TELEMETRY_ENDPOINT", candidate)
    selected = telemetry_config()
    assert selected.endpoint == ENDPOINT
    assert "using default" in diagnostics.getvalue()
    assert "Traceback" not in diagnostics.getvalue()


def test_telemetry_configuration_preserves_query_without_logging_it(diagnostics, telemetry_config):
    selected = telemetry_config()
    assert selected.endpoint == ENDPOINT
    assert "https://collector.example:8443/events" in diagnostics.getvalue()


def test_native_httpx_diagnostic_strips_every_credential_component(diagnostics, telemetry_config):
    telemetry_config()
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as client:
        client.post(UNTRUSTED)
    assert "HTTP Request: POST https://collector.example/events" in diagnostics.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, httpx.ConnectError, ValueError])
async def test_auth_request_logs_and_errors_hide_url_credentials(diagnostics, monkeypatch, failure):
    service = ApiKeyService(
        ENDPOINT, service_token_header="X-Service-Token", service_token="service-token"
    )
    monkeypatch.setattr(service, "MAX_RETRIES", 1)
    seen = []

    def respond(request):
        seen.append(request)
        assert str(request.url) == ENDPOINT
        assert request.headers["X-Service-Token"] == "service-token"
        if failure is not None:
            raise failure(f"{UNTRUSTED} {KEY}")
        return httpx.Response(200, json={"valid": True, "user_id": "owner"})

    service._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        result = await service.validate(KEY)
        assert result.valid is (failure is None)
        assert len(seen) == (2 if failure is httpx.ConnectError else 1)
        assert (
            "collector.example" in diagnostics.getvalue()
            if failure is None
            else "error" in diagnostics.getvalue()
        )
    finally:
        await service.aclose()
        ApiKeyService._instance = None


@pytest.mark.parametrize(
    "candidate",
    [
        UNTRUSTED,
        f"https://collector.example:{USER}/?unknown={QUERY}",
        f"https://[{USER}/?unknown={QUERY}",
    ],
)
def test_auth_url_rejection_does_not_expose_native_exception(candidate, diagnostics):
    with pytest.raises(ValueError) as failure:
        ApiKeyService(candidate)
    for secret in (USER, QUERY, FRAGMENT):
        assert secret not in str(failure.value)
    assert failure.value.__suppress_context__ or failure.value.__context__ is None


@pytest.mark.parametrize("failure", [False, True])
def test_telemetry_native_httpx_logs_hide_query(diagnostics, telemetry_config, failure):
    telemetry = importlib.import_module("core.telemetry")
    collector = TelemetryCollector.__new__(TelemetryCollector)
    collector.config = telemetry_config()
    seen = []

    def respond(request):
        seen.append(str(request.url))
        if failure:
            raise httpx.ConnectError(f"cannot send {UNTRUSTED}")
        return httpx.Response(200)

    with telemetry.httpx.Client(transport=httpx.MockTransport(respond)) as client:
        collector._http_client = client
        collector._send_telemetry(record())
    assert seen == [ENDPOINT]
    assert (
        "Telemetry send failed: ConnectError" in diagnostics.getvalue()
        if failure
        else "HTTP Request" in diagnostics.getvalue()
    )


@pytest.mark.parametrize("failure", ["url", "http", "request"])
def test_urllib_exceptions_do_not_echo_endpoint(
    diagnostics, telemetry_config, monkeypatch, failure
):
    telemetry = importlib.import_module("core.telemetry")
    collector = TelemetryCollector.__new__(TelemetryCollector)
    collector.config = telemetry_config()
    monkeypatch.setattr(telemetry, "httpx", None)
    seen = []

    def fail(request, **kwargs):
        seen.append(request.full_url)
        if failure == "http":
            raise urllib.error.HTTPError(ENDPOINT, 500, UNTRUSTED, None, None)
        if failure == "request":
            raise ValueError(UNTRUSTED)
        raise urllib.error.URLError(UNTRUSTED)

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    collector._send_telemetry(record())
    assert seen == [ENDPOINT]
    assert "Telemetry send failed" in diagnostics.getvalue()


def test_worker_exception_traceback_never_reveals_endpoint(diagnostics, monkeypatch):
    collector = TelemetryCollector.__new__(TelemetryCollector)
    collector._queue = queue.Queue()
    collector._queue.put(record())
    collector._shutdown = False
    collector._http_client = None
    collector.config = SimpleNamespace(timeout=1.0)
    telemetry = importlib.import_module("core.telemetry")
    monkeypatch.setattr(telemetry, "httpx", None)

    def fail(_record):
        collector._shutdown = True
        raise RuntimeError(UNTRUSTED)

    monkeypatch.setattr(collector, "_send_telemetry", fail)
    collector._worker_loop()
    assert "Telemetry worker send failed" in diagnostics.getvalue()
    assert "Traceback" not in diagnostics.getvalue()


def test_main_startup_diagnostic_hides_auth_query(diagnostics, monkeypatch):
    main = importlib.import_module("main")
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(config, "api_key_validation_url", ENDPOINT)
    monkeypatch.setattr(main, "register_all_tools", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "register_all_resources", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "ApiKeyService", lambda **kwargs: None)
    main.create_mcp_server(False)
    assert "validation URL: https://collector.example:8443/events" in diagnostics.getvalue()
