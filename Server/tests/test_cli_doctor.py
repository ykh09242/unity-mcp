"""The doctor CLI reports its own SDK proof, without inspecting a desktop client."""

import json

from click.testing import CliRunner
import pytest

from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import


@pytest.fixture(autouse=True)
def disposable_auth(monkeypatch):
    monkeypatch.setenv("UNITY_MCP_LOCAL_AUTH_TOKEN", "disposable-module-fixture-token")
    monkeypatch.delenv("UNITY_MCP_LOCAL_AUTH_TOKEN_FILE", raising=False)


@pytest.mark.parametrize("target", [None, "aaaa1111", "6400", "Project@", "bad@target@twice"])
def test_doctor_rejects_nonexact_target_before_auth(monkeypatch, target):
    from cli.main import cli
    import core.local_auth as auth

    monkeypatch.setattr(
        auth, "read_local_auth_token", lambda *_: pytest.fail("invalid target read auth")
    )
    args = ["--format", "json"]
    if target is not None:
        args += ["--instance", target]
    result = CliRunner().invoke(cli, [*args, "doctor"])
    assert result.exit_code == 2
    receipt = json.loads(result.stdout)
    assert receipt["scope"] == "diagnostic_sdk"
    assert receipt["external_client_verified"] is False
    assert receipt["reason"] == "invalid_target"


def test_doctor_help_explains_actual_scope():
    from cli.main import cli

    result = CliRunner().invoke(cli, ["doctor", "--help"])
    assert result.exit_code == 0
    assert "--protocol" in result.output
    assert "desktop" in result.output.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_sdk_diagnostic_proves_only_its_own_target(command_hub, monkeypatch, mode):
    from fastmcp import Client
    from core.server_build import ServerBuild
    from cli.utils.config import CLIConfig
    import cli.utils.doctor_connection as connection
    import services.doctor as doctor
    from tests.http.test_doctor import doctor_server, observations, install_native_replies

    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "aaaa1111", "6000")
    values = observations()
    values["get_editor_state"]["unity"]["instance_id"] = "Fixture@aaaa1111"
    await install_native_replies(command_hub, values)
    monkeypatch.setattr(doctor, "RUNNING_SERVER", ServerBuild("1.2.0", "a" * 40, "c" * 32))
    server = doctor_server()
    monkeypatch.setattr(
        connection,
        "_make_client",
        lambda cfg, protocol, factory, headers: Client(server, mode=protocol, cache=False),
    )
    receipt = await connection.diagnose(CLIConfig(unity_instance="Fixture@aaaa1111"), mode)
    assert receipt.exit_code == 0
    assert receipt.connection_verified is True
    assert receipt.external_client_verified is False
    assert receipt.scope == "diagnostic_sdk"
    assert receipt.observed_protocol in ("2026-07-28", "2025-11-25")
    assert len([item for item in command_hub.sent if item["type"] == "execute"]) == 5


@pytest.mark.asyncio
async def test_discovery_missing_doctor_is_incomplete_not_native_success(monkeypatch):
    from fastmcp import Client, FastMCP
    from cli.utils.config import CLIConfig
    import cli.utils.doctor_connection as connection

    server = FastMCP("old-server")
    monkeypatch.setattr(
        connection,
        "_make_client",
        lambda cfg, protocol, factory, headers: Client(server, mode=protocol, cache=False),
    )
    receipt = await connection.diagnose(CLIConfig(unity_instance="Fixture@aaaa1111"), "auto")
    assert receipt.exit_code == 3
    assert receipt.reason == "doctor_unavailable"
    assert receipt.connection_verified is False


@pytest.mark.parametrize("follow_redirects", [True, False])
def test_factory_overrides_sdk_redirect_request(follow_redirects):
    from cli.utils.doctor_connection import DoctorHttpFactory

    client = DoctorHttpFactory()(follow_redirects=follow_redirects)
    assert client.follow_redirects is False
    import anyio

    anyio.run(client.aclose)


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", ["expired", "future", "unbounded"])
async def test_factory_never_admits_http_after_original_deadline(budget):
    import anyio
    import httpx2
    from cli.utils.doctor_connection import DoctorHttpFactory

    calls = []
    deadline = (
        None
        if budget == "unbounded"
        else anyio.current_time() + (-1 if budget == "expired" else 10)
    )
    factory = DoctorHttpFactory(deadline=deadline)

    def receive(request):
        calls.append(request.method)
        return httpx2.Response(200, content=b"owned")

    async with factory(transport=httpx2.MockTransport(receive)) as client:
        if budget == "expired":
            with pytest.raises(TimeoutError):
                await client.delete("http://fixture.invalid/mcp")
            assert calls == []
        else:
            response = await client.get("http://fixture.invalid/mcp")
            assert response.status_code == 200 and calls == ["GET"]


@pytest.mark.asyncio
async def test_stream_limit_closes_original_response_without_echoing_bytes():
    import httpx2
    from cli.utils.doctor_connection import DoctorHttpFactory, DiagnosticLimit

    factory = DoctorHttpFactory(response_limit=4)
    async with factory(
        transport=httpx2.MockTransport(
            lambda request: httpx2.Response(200, content=b"secret-payload")
        )
    ) as client:
        with pytest.raises(DiagnosticLimit):
            await client.get("http://fixture.invalid/mcp")
    assert "secret" not in str(DiagnosticLimit())


from contextlib import asynccontextmanager
import asyncio  # noqa: ANYIO_OK -- run the real ASGI server alongside SDK requests.
import socket


@asynccontextmanager
async def live_mcp(server, trace, legacy_only=False, required_token=None, redirect_status=None):
    """Serve actual loopback HTTP; record method names only, never request credentials."""
    import uvicorn
    from starlette.responses import JSONResponse

    app = server.http_app()

    async def traced(scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            return await app(scope, receive, send)
        if redirect_status is not None:
            trace.append("contact:" + scope["path"])
            if scope["path"] == "/mcp":
                response = JSONResponse(
                    {}, status_code=redirect_status, headers={"Location": "/mcp-redirected"}
                )
                return await response(scope, receive, send)
        if (
            required_token is not None
            and dict(scope["headers"]).get(b"x-unity-mcp-token") != required_token.encode()
        ):
            response = JSONResponse({"error": "rejected-secret-body"}, status_code=401)
            return await response(scope, receive, send)
        chunks = []
        while True:
            message = await receive()
            chunks.append(message.get("body", b""))
            if not message.get("more_body"):
                break
        raw = b"".join(chunks)
        method = json.loads(raw).get("method")
        trace.append(method)
        if legacy_only and method == "server/discover":
            response = JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": json.loads(raw)["id"],
                    "error": {"code": -32601, "message": "Method not found"},
                }
            )
            return await response(scope, receive, send)
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": raw, "more_body": False}
            return await receive()

        return await app(scope, replay, send)

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        host = uvicorn.Server(
            uvicorn.Config(
                traced,
                lifespan="on",
                log_config=None,
                access_log=False,
                timeout_keep_alive=0.1,
                timeout_graceful_shutdown=0.2,
            )
        )
        task = asyncio.create_task(host.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(3):
                while not host.started:
                    await asyncio.sleep(0)
            yield port
        finally:
            host.should_exit = True
            await asyncio.wait_for(task, 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,legacy_only", [("auto", False), ("legacy", False), ("auto", True)])
@pytest.mark.parametrize("project_name", ["Fixture", "My Project", "Project #One"])
async def test_real_click_sdk_loopback_discovery_and_safe_read(
    command_hub, monkeypatch, mode, legacy_only, project_name
):
    import anyio
    from cli.main import cli
    from core.server_build import ServerBuild
    import services.doctor as doctor
    from tests.http.test_doctor import doctor_server, observations, install_native_replies

    monkeypatch.setenv("UNITY_MCP_LOCAL_AUTH_TOKEN", "disposable-fixture-token")
    monkeypatch.delenv("UNITY_MCP_LOCAL_AUTH_TOKEN_FILE", raising=False)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", project_name, "aaaa1111", "6000")
    values = observations()
    values["get_editor_state"]["unity"]["instance_id"] = f"{project_name}@aaaa1111"
    await install_native_replies(command_hub, values)
    monkeypatch.setattr(doctor, "RUNNING_SERVER", ServerBuild("1.2.0", "a" * 40, "c" * 32))
    import cli.utils.doctor_connection as connection

    streams = []
    original_init = connection._BoundedStream.__init__

    def track_stream(stream, *args):
        original_init(stream, *args)
        streams.append(stream)

    monkeypatch.setattr(connection._BoundedStream, "__init__", track_stream)
    trace = []
    async with live_mcp(
        doctor_server(), trace, legacy_only, required_token="disposable-fixture-token"
    ) as port:
        result = await anyio.to_thread.run_sync(
            lambda: CliRunner().invoke(
                cli,
                [
                    "--port",
                    str(port),
                    "--instance",
                    f"{project_name}@aaaa1111",
                    "--format",
                    "json",
                    "doctor",
                    "--protocol",
                    mode,
                ],
            )
        )
    assert result.exit_code == 0, (result.exception, result.stdout)
    assert streams and all(stream.response.is_closed for stream in streams)
    receipt = json.loads(result.stdout)
    assert receipt["connection_verified"] is True
    assert receipt["external_client_verified"] is False
    assert "resources/list" in trace and "resources/read" in trace
    if mode == "legacy" or legacy_only:
        assert "initialize" in trace
        assert "notifications/initialized" in trace
        assert receipt["observed_protocol"] != "2026-07-28"
    else:
        assert "server/discover" in trace
        assert "initialize" not in trace
        assert receipt["observed_protocol"] == "2026-07-28"
    assert "disposable-fixture-token" not in result.stdout + result.stderr


@pytest.mark.asyncio
async def test_redirect_never_contacts_second_origin_or_forwards_auth():
    import httpx2
    from cli.utils.doctor_connection import DoctorHttpFactory

    requests = []

    def redirect(request):
        requests.append((str(request.url), request.headers.get("X-Unity-MCP-Token")))
        return httpx2.Response(307, headers={"Location": "http://other.invalid/mcp"})

    async with DoctorHttpFactory()(
        headers={"X-Unity-MCP-Token": "secret"},
        follow_redirects=True,
        transport=httpx2.MockTransport(redirect),
    ) as client:
        response = await client.get("http://selected.invalid/mcp")
    assert response.status_code == 307
    assert requests == [("http://selected.invalid/mcp", "secret")]


@pytest.mark.asyncio
async def test_chunked_and_compressed_responses_obey_decoded_limit():
    import gzip
    import httpx2
    from cli.utils.doctor_connection import DoctorHttpFactory, DiagnosticLimit

    closed = []

    class Chunks(httpx2.AsyncByteStream):
        async def __aiter__(self):
            yield gzip.compress(b"x" * 1000)

        async def aclose(self):
            closed.append(True)

    async with DoctorHttpFactory(response_limit=50)(
        transport=httpx2.MockTransport(
            lambda request: httpx2.Response(
                200, headers={"content-encoding": "gzip"}, stream=Chunks()
            )
        )
    ) as client:
        with pytest.raises(DiagnosticLimit):
            await client.get("http://fixture.invalid/mcp")
    assert closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "instances,expected",
    [([], "unity_unavailable"), ([{"id": "Other@bbbb2222"}], "target_unavailable")],
)
async def test_missing_target_classifies_only_from_current_visible_inventory(
    command_hub, monkeypatch, instances, expected
):
    from fastmcp import Client
    from cli.utils.config import CLIConfig
    import cli.utils.doctor_connection as connection
    from tests.http.test_doctor import doctor_server

    server = doctor_server()

    @server.resource("mcpforunity://instances")
    async def inventory():
        return json.dumps({"success": True, "instances": instances})

    monkeypatch.setattr(
        connection,
        "_make_client",
        lambda cfg, protocol, factory, headers: Client(server, mode=protocol, cache=False),
    )
    receipt = await connection.diagnose(CLIConfig(unity_instance="Missing@cccc3333"), "legacy")
    assert receipt.exit_code == 1
    assert receipt.reason == expected
    assert receipt.connection_verified is False
    assert command_hub.sent == []


@pytest.mark.asyncio
async def test_unclassified_resource_error_is_unknown_and_redacted(monkeypatch):
    from fastmcp import Client, FastMCP
    from cli.utils.config import CLIConfig
    import cli.utils.doctor_connection as connection

    server = FastMCP("unknown-backend")

    @server.resource("mcpforunity://doctor")
    async def broken():
        raise RuntimeError("private-secret-at-https://user:password@host")

    monkeypatch.setattr(
        connection,
        "_make_client",
        lambda cfg, protocol, factory, headers: Client(server, mode=protocol, cache=False),
    )
    receipt = await connection.diagnose(CLIConfig(unity_instance="Fixture@aaaa1111"), "legacy")
    assert receipt.exit_code == 3
    assert receipt.reason == "source_unavailable"
    assert "secret" not in receipt.model_dump_json()


@pytest.mark.asyncio
async def test_shared_deadline_closes_sdk_session_and_keeps_native_residual_unknown(monkeypatch):
    import anyio
    from fastmcp import Client, FastMCP
    from cli.utils.config import CLIConfig
    import cli.utils.doctor_connection as connection

    server = FastMCP("slow-read")
    stopped = anyio.Event()

    @server.resource("mcpforunity://doctor")
    async def slow():
        try:
            await anyio.sleep_forever()
        finally:
            stopped.set()

    client = Client(server, mode="legacy", cache=False)
    monkeypatch.setattr(connection, "_make_client", lambda cfg, protocol, factory, headers: client)
    started = anyio.current_time()
    receipt = await connection.diagnose(
        CLIConfig(unity_instance="Fixture@aaaa1111", timeout=1), "legacy"
    )
    assert anyio.current_time() - started < 2.2
    assert receipt.exit_code == 3 and receipt.reason == "deadline"
    assert not client.is_connected()
    assert client._session_state.session_task is None
    assert stopped.is_set()  # This fixture ended; do not generalize to external native work.


@pytest.mark.asyncio
async def test_missing_sdk_is_dependency_failure_before_connection(monkeypatch):
    from importlib import metadata
    from cli.utils.config import CLIConfig
    import cli.utils.doctor_connection as connection

    def missing(package):
        raise metadata.PackageNotFoundError(package)

    monkeypatch.setattr(connection.metadata, "version", missing)
    monkeypatch.setattr(
        connection, "_make_client", lambda *_: pytest.fail("missing dependency connected")
    )
    receipt = await connection.diagnose(CLIConfig(unity_instance="Fixture@aaaa1111"))
    assert receipt.exit_code == 4 and receipt.reason == "dependency_missing"


@pytest.mark.asyncio
async def test_unknown_native_read_cannot_be_promoted_by_matching_source(monkeypatch):
    from fastmcp import Client, FastMCP
    from cli.utils.config import CLIConfig
    import cli.utils.doctor_connection as connection
    from models.doctor import (
        DoctorCheck,
        DoctorLimits,
        DoctorReceipt,
        DoctorServer,
        DoctorResourceResponse,
    )

    server = FastMCP("ambiguous-read")
    check = DoctorCheck(status="unknown", reason="source_unavailable", next_action="retry_later")
    data = DoctorReceipt(
        server=DoctorServer(version="1.2.0", source_commit="a" * 40, server_id="c" * 32),
        checks={key: check for key in ("target", "native_read", "source", "readiness")},
        limits=DoctorLimits(
            query_timeout_seconds=5.0,
            query_cleanup_grace_seconds=1.0,
            query_max_elapsed_seconds=6.0,
        ),
    )

    @server.resource("mcpforunity://doctor")
    async def resource():
        return DoctorResourceResponse(data=data).model_dump_json()

    monkeypatch.setattr(
        connection,
        "_make_client",
        lambda cfg, protocol, factory, headers: Client(server, mode=protocol, cache=False),
    )
    receipt = await connection.diagnose(CLIConfig(unity_instance="Fixture@aaaa1111"))
    assert receipt.exit_code == 3 and receipt.reason == "source_unavailable"


@pytest.mark.parametrize(
    "raw",
    [
        {
            "nest": {
                "nest": {
                    "nest": {
                        "nest": {
                            "nest": {
                                "nest": {
                                    "nest": {
                                        "nest": {"nest": {"nest": {"nest": {"nest": {"nest": 1}}}}}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
        {"entries": list(range(513))},
    ],
)
def test_receipt_depth_and_node_limits(raw):
    from cli.utils.doctor_connection import DiagnosticLimit, _check_json_limits

    with pytest.raises(DiagnosticLimit):
        _check_json_limits(raw)


@pytest.mark.asyncio
@pytest.mark.parametrize("catalog", ["cycle", "overflow", "deep", "binary", "extra"])
async def test_incomplete_or_invalid_peer_receipt_never_becomes_success(monkeypatch, catalog):
    from fastmcp import Client, FastMCP
    from fastmcp.server.middleware import Middleware
    from cli.utils.config import CLIConfig
    import cli.utils.doctor_connection as connection
    from mcp_types import ListResourcesResult, Resource

    server = FastMCP("hostile-catalog")

    @server.resource("mcpforunity://doctor")
    async def resource():
        if catalog == "binary":
            return b"not-a-json-receipt"
        if catalog == "deep":
            value = "secret"
            for _ in range(15):
                value = {"value": value}
            return json.dumps(value)
        return json.dumps({"success": True, "data": {"extra": "https://user:secret@host"}})

    if catalog in ("cycle", "overflow"):

        class Catalog(Middleware):
            async def on_list_resources(self, context, call_next):
                count = 513 if catalog == "overflow" else 1
                return ListResourcesResult(
                    resources=[
                        Resource(uri=f"fixture://entry/{index}", name="entry")
                        for index in range(count)
                    ],
                    nextCursor="repeat",
                )

        server.add_middleware(Catalog())
    monkeypatch.setattr(
        connection,
        "_make_client",
        lambda cfg, protocol, factory, headers: Client(server, mode=protocol, cache=False),
    )
    receipt = await connection.diagnose(CLIConfig(unity_instance="Fixture@aaaa1111"), "legacy")
    assert receipt.exit_code == 3
    assert receipt.connection_verified is False
    assert "secret" not in receipt.model_dump_json()


@pytest.mark.asyncio
async def test_actual_http_auth_rejection_is_bounded_and_redacted(monkeypatch):
    from fastmcp import FastMCP
    from cli.utils.config import CLIConfig
    from cli.utils.doctor_connection import diagnose

    monkeypatch.setenv("UNITY_MCP_LOCAL_AUTH_TOKEN", "fixture-stale-token")
    async with live_mcp(FastMCP("protected"), [], required_token="different-token") as port:
        receipt = await diagnose(
            CLIConfig(port=port, unity_instance="Fixture@aaaa1111", verbose=True)
        )
    assert receipt.exit_code == 1 and receipt.reason == "auth_required"
    wire = receipt.model_dump_json()
    assert "fixture-stale-token" not in wire
    assert "rejected-secret-body" not in wire


@pytest.mark.asyncio
async def test_actual_refused_socket_is_server_unreachable(monkeypatch):
    from cli.utils.config import CLIConfig
    from cli.utils.doctor_connection import diagnose

    monkeypatch.setenv("UNITY_MCP_LOCAL_AUTH_TOKEN", "disposable-fixture-token")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    receipt = await diagnose(
        CLIConfig(port=port, unity_instance="Fixture@aaaa1111", timeout=3), "legacy"
    )
    assert receipt.exit_code == 1 and receipt.reason == "server_unreachable"


@pytest.mark.asyncio
async def test_actual_http_read_timeout_closes_owned_sdk_streams(monkeypatch):
    import anyio
    import httpx2
    from fastmcp import FastMCP
    from cli.utils.config import CLIConfig
    import cli.utils.doctor_connection as connection

    monkeypatch.setenv("UNITY_MCP_LOCAL_AUTH_TOKEN", "disposable-timeout-token")
    server = FastMCP("actual-slow-http")

    @server.resource("mcpforunity://doctor")
    async def slow():
        await anyio.sleep_forever()

    clients = []
    streams = []
    wire_admissions = []
    make_client = connection._make_client
    init_stream = connection._BoundedStream.__init__
    send_request = httpx2.AsyncHTTPTransport.handle_async_request

    def capture_client(*args):
        client = make_client(*args)
        clients.append(client)
        return client

    def capture_stream(stream, *args):
        init_stream(stream, *args)
        streams.append(stream)

    async def capture_admission(transport, request):
        wire_admissions.append((request.method, anyio.current_time()))
        return await send_request(transport, request)

    monkeypatch.setattr(connection, "_make_client", capture_client)
    monkeypatch.setattr(connection._BoundedStream, "__init__", capture_stream)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", capture_admission)
    async with live_mcp(server, []) as port:
        started = anyio.current_time()
        receipt = await connection.diagnose(
            CLIConfig(port=port, unity_instance="Fixture@aaaa1111", timeout=1), "legacy"
        )
        elapsed = anyio.current_time() - started
        assert elapsed < 2.2
        assert all(stream.response.is_closed for stream in streams)
        assert all(not client.is_connected() for client in clients)
        assert wire_admissions and clients
        assert all(admitted < clients[0].factory.deadline for _, admitted in wire_admissions), (
            "SDK cancellation/termination must not open HTTP after the original read deadline"
        )
    assert receipt.exit_code == 3
    assert receipt.reason == "deadline"
    assert receipt.residual_server_work == "unknown"


@pytest.mark.parametrize("target,expected", [("Fixture@aaaa1111", 4), ("prefix", 2)])
def test_cli_missing_sdk_import_still_returns_safe_json(monkeypatch, target, expected):
    import builtins
    from cli.main import cli

    real_import = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "cli.utils.doctor_connection":
            raise ModuleNotFoundError("private-import-detail", name="httpx2")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    result = CliRunner().invoke(cli, ["--instance", target, "--format", "json", "doctor"])
    assert result.exit_code == expected
    receipt = json.loads(result.stdout)
    assert receipt["reason"] == ("dependency_missing" if expected == 4 else "invalid_target")
    assert "private-import-detail" not in result.stdout + result.stderr


@pytest.mark.asyncio
async def test_slow_real_sdk_teardown_leaves_no_detached_owned_tasks(monkeypatch):
    import anyio
    import httpx2
    from fastmcp import FastMCP
    from cli.utils.config import CLIConfig
    import cli.utils.doctor_connection as connection

    # Actual public MCP client/session/HTTP transport, including caller-owned HTTP aclose.
    # Historical FastMCP detached-stopper RED source is retained in review-baseline-source.
    closing = anyio.Event()
    closed = anyio.Event()
    original_http = httpx2.AsyncHTTPTransport
    clients = []
    streams = []
    make_client = connection._make_client
    init_stream = connection._BoundedStream.__init__

    class SlowHTTPTransport(original_http):
        async def aclose(self):
            closing.set()
            try:
                await anyio.sleep(10)
            finally:
                with anyio.CancelScope(shield=True):
                    await super().aclose()
                closed.set()

    def capture_client(*args):
        client = make_client(*args)
        clients.append(client)
        return client

    def capture_stream(stream, *args):
        init_stream(stream, *args)
        streams.append(stream)

    monkeypatch.setattr(httpx2, "AsyncHTTPTransport", SlowHTTPTransport)
    monkeypatch.setattr(connection, "_make_client", capture_client)
    monkeypatch.setattr(connection._BoundedStream, "__init__", capture_stream)
    async with live_mcp(FastMCP("slow-owned-http-close"), []) as port:
        before = set(asyncio.all_tasks())
        started = anyio.current_time()
        receipt = await connection.diagnose(
            CLIConfig(port=port, unity_instance="Fixture@aaaa1111", timeout=1), "legacy"
        )
        assert anyio.current_time() - started < 2.2
        assert closing.is_set() and closed.is_set()
        assert streams and all(stream.response.is_closed for stream in streams)
        assert clients and all(not client.is_connected() for client in clients)
        remaining = [
            task
            for task in asyncio.all_tasks() - before
            if not task.done()
            and "/mcp/client/" in task.get_coro().cr_code.co_filename.replace("\\", "/")
        ]
        assert remaining == []
    assert receipt.exit_code == 3 and receipt.reason == "deadline"
    assert receipt.residual_server_work == "unknown"


@pytest.mark.asyncio
async def test_auth_wait_uses_original_deadline_and_discards_late_result(monkeypatch):
    import anyio
    import threading
    from cli.utils.config import CLIConfig
    import cli.utils.doctor_connection as connection

    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    requests = []

    def slow_auth(config):
        started.set()
        release.wait(3)
        finished.set()
        return {"X-Unity-MCP-Token": "late-disposable-secret"}

    original = connection._BoundedTransport.handle_async_request

    async def request(transport, message):
        requests.append(True)
        return await original(transport, message)

    monkeypatch.setattr(connection, "_auth_headers", slow_auth)
    monkeypatch.setattr(connection._BoundedTransport, "handle_async_request", request)
    watchdog = threading.Timer(2.5, release.set)
    watchdog.start()
    try:
        clock = anyio.current_time()
        receipt = await connection.diagnose(
            CLIConfig(unity_instance="Fixture@aaaa1111", timeout=1), "legacy"
        )
        elapsed = anyio.current_time() - clock
        assert started.is_set()
        assert elapsed < 2.2
        assert requests == []
        assert receipt.exit_code == 3 and receipt.reason == "deadline"
        assert receipt.residual_auth_io == "unknown"
        assert "late-disposable-secret" not in receipt.model_dump_json()
        release.set()
        assert await anyio.to_thread.run_sync(finished.wait, 1)
        await anyio.lowlevel.checkpoint()
        assert requests == []  # A completed late loader result cannot resume HTTP setup.
    finally:
        release.set()
        watchdog.cancel()
        watchdog.join(timeout=1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "comparison,source_status",
    [("mismatch", "passed"), ("mismatch", "unknown"), ("matched", "unknown")],
)
async def test_contradictory_source_receipt_never_becomes_exit_zero(
    monkeypatch, comparison, source_status
):
    from fastmcp import Client, FastMCP
    from cli.utils.config import CLIConfig
    import cli.utils.doctor_connection as connection
    from models.doctor import (
        DoctorCheck,
        DoctorLimits,
        DoctorPackage,
        DoctorReceipt,
        DoctorResourceResponse,
        DoctorServer,
        DoctorTarget,
    )

    server = FastMCP("contradictory-source")
    passed = DoctorCheck(status="passed", reason="observed", next_action="none")
    unknown = DoctorCheck(
        status="unknown", reason="comparison_unknown", next_action="verify_selected_source"
    )
    data = DoctorReceipt(
        connection_verified=True,
        server=DoctorServer(version="1.2.0", source_commit="a" * 40, server_id="c" * 32),
        target=DoctorTarget(
            instance_id="Fixture@aaaa1111", project_hash="aaaa1111", generation="d" * 64
        ),
        package=DoctorPackage(
            name="com.ykh09242.unity-mcp",
            version="1.2.1",
            effective_source_kind="package_pin",
            effective_server_commit=("b" if comparison == "mismatch" else "a") * 40,
        ),
        source_comparison=comparison,
        checks={
            "target": passed,
            "native_read": passed,
            "source": passed if source_status == "passed" else unknown,
            "readiness": passed,
        },
        limits=DoctorLimits(
            query_timeout_seconds=5.0,
            query_cleanup_grace_seconds=1.0,
            query_max_elapsed_seconds=6.0,
        ),
    )

    @server.resource("mcpforunity://doctor")
    async def resource():
        return DoctorResourceResponse(data=data).model_dump_json()

    monkeypatch.setattr(
        connection,
        "_make_client",
        lambda cfg, protocol, factory, headers: Client(server, mode=protocol, cache=False),
    )
    result = await connection.diagnose(CLIConfig(unity_instance="Fixture@aaaa1111"), "legacy")
    assert result.exit_code == 3
    assert result.reason == "invalid_receipt"


@pytest.mark.asyncio
@pytest.mark.parametrize("redirect_status", [307, 308])
async def test_actual_sdk_never_follows_same_origin_redirect(monkeypatch, redirect_status):
    from fastmcp import FastMCP
    from cli.utils.config import CLIConfig
    from cli.utils.doctor_connection import diagnose

    monkeypatch.setenv("UNITY_MCP_LOCAL_AUTH_TOKEN", "disposable-redirect-token")
    trace = []
    async with live_mcp(
        FastMCP("redirect-fixture"), trace, redirect_status=redirect_status
    ) as port:
        receipt = await diagnose(
            CLIConfig(port=port, unity_instance="Fixture@aaaa1111", timeout=2), "legacy"
        )
    assert "contact:/mcp" in trace
    assert "contact:/mcp-redirected" not in trace
    assert receipt.exit_code != 0
