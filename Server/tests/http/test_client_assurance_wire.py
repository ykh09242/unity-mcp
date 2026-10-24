"""Official MCP clients exercise production registration, middleware and real wires.

The WebSocket peers emulate Unity, not a native Editor. No routing, discovery,
authentication, or MCP dispatch middleware is replaced by the fixtures.
"""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
import json
import os
from pathlib import Path
import sys
import ssl
from threading import Event, Thread

import anyio
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
import httpx2
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
import pytest
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from core.local_auth import LOCAL_AUTH_HEADER
from tests.http.client_assurance_legacy import LegacyPeer
from tests.http.test_local_control_wire import local_server_process

MODERN_VERSION = "2026-07-28"
CATALOG_SIZE = 512


@asynccontextmanager
async def wire_client(server, mode: str, api_key: str | None = None) -> AsyncIterator[Client]:
    """Use the official client without its catalog cache masking a server regression."""
    url, _, token = server
    headers = {"X-API-Key": api_key} if api_key is not None else {LOCAL_AUTH_HEADER: token}
    async with httpx2.AsyncClient(headers=headers, trust_env=False, timeout=20) as http:
        async with Client(
            streamable_http_client(f"{url}/mcp", http_client=http),
            mode=MODERN_VERSION if mode == "modern" else "legacy",
            read_timeout_seconds=20,
            cache=None,
        ) as client:
            yield client


@pytest.fixture(scope="module")
def assurance_server(tmp_path_factory):
    """One real production server; clients and plugin sockets remain independent."""
    with pytest.MonkeyPatch.context() as environment:
        environment.setenv("UNITY_MCP_TOOL_PROFILE", "full")
        with local_server_process(
            tmp_path_factory.mktemp("client-assurance"), module="tests.http.client_assurance_server"
        ) as server:
            yield server


class ControlledPlugin:
    """A wire-only Unity peer retaining counters and one held command, not a transcript."""

    def __init__(self, server, index: int, api_key: str | None = None):
        self.server = server
        self.index = index
        self.hash = f"assurance{index}"
        self.instance = f"Assurance{index}@{self.hash}"
        self.commands = 0
        self.cancelled = Event()
        self.entered = Event()
        self.hold_next = False
        self.failure = None
        self.api_key = api_key

    @contextmanager
    def connected(self) -> Iterator["ControlledPlugin"]:
        """Register before yielding and join the owned reader on every exit path."""
        _, port, token = self.server
        headers = (
            {"X-API-Key": self.api_key} if self.api_key is not None else {LOCAL_AUTH_HEADER: token}
        )
        with connect(
            f"ws://127.0.0.1:{port}/hub/plugin",
            additional_headers=headers,
            proxy=None,
        ) as socket:
            assert json.loads(socket.recv(timeout=10))["type"] == "welcome"
            socket.send(
                json.dumps(
                    {
                        "type": "register",
                        "project_name": f"Assurance{self.index}",
                        "project_hash": self.hash,
                        "unity_version": "6000.0",
                        "capabilities": ["command_cancel_v1"],
                    }
                )
            )
            registered = json.loads(socket.recv(timeout=10))
            assert registered["type"] == "registered"
            if self.api_key is not None:
                socket.send(
                    json.dumps({"type": "register_tools", "tools": [{"name": "manage_scene"}]})
                )

            def respond() -> None:
                try:
                    for raw in socket:
                        message = json.loads(raw)
                        match message["type"]:
                            case "ping":
                                socket.send(
                                    json.dumps(
                                        {"type": "pong", "session_id": registered["session_id"]}
                                    )
                                )
                            case "execute":
                                self.commands += 1
                                if self.hold_next:
                                    self.hold_next = False
                                    self.entered.set()
                                    continue
                                socket.send(
                                    json.dumps(
                                        {
                                            "type": "command_result",
                                            "id": message["id"],
                                            "result": {
                                                "success": True,
                                                "data": {"name": self.hash},
                                            },
                                        }
                                    )
                                )
                            case "cancel":
                                self.cancelled.set()
                            case other:
                                raise AssertionError(f"Unexpected plugin frame: {other}")
                except ConnectionClosed:
                    return
                except (AssertionError, ValueError, KeyError) as error:
                    self.failure = error

            reader = Thread(target=respond, name=f"assurance-peer-{self.index}", daemon=True)
            reader.start()
            try:
                yield self
            finally:
                socket.close()
                reader.join(timeout=10)
                assert not reader.is_alive(), "Owned fixture reader survived cleanup"
                assert self.failure is None, self.failure


async def state(client: Client) -> dict:
    result = await client.read_resource("assurance://retention", cache_mode="refresh")
    return json.loads(result.contents[0].text)


async def read_scene(client: Client, instance: str) -> str:
    result = await client.call_tool(
        "manage_scene", {"action": "get_active", "unity_instance": instance}
    )
    assert not result.is_error, result
    assert result.structured_content["success"] is True
    return result.structured_content["data"]["name"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["modern", "legacy"])
async def test_official_clients_route_two_targets_across_reconnect_and_soak(assurance_server, mode):
    # Given: two real plugin sockets and independent official MCP connections.
    rounds = int(os.environ.get("UNITY_MCP_ASSURANCE_ROUNDS", "64"))
    assert 1 <= rounds <= 10000
    with (
        ControlledPlugin(assurance_server, 1).connected() as first,
        ControlledPlugin(assurance_server, 2).connected() as second,
    ):
        # When: calls overlap across targets, then new clients reconnect to the same server.
        for _ in range(2):
            async with wire_client(assurance_server, mode) as client:
                before = await state(client)
                assert (
                    Path(before["source"]).resolve().parents[2]
                    == Path(__file__).resolve().parents[3]
                )
                assert client.protocol_version == (
                    MODERN_VERSION if mode == "modern" else "2025-11-25"
                )

                async def exercise(peer: ControlledPlugin) -> None:
                    for _ in range(rounds):
                        assert await read_scene(client, peer.instance) == peer.hash

                async with anyio.create_task_group() as tasks:
                    tasks.start_soon(exercise, first)
                    tasks.start_soon(exercise, second)
                after = await state(client)
                # Then: no sessionless per-request state or pending results accumulates.
                assert after["pending"] == after["retained_results"] == 0
                assert after["result_bytes"] == after["raw_results"] == 0
                assert after["entries"] == before["entries"]
                assert after["bytes"] == before["bytes"]
        assert first.commands == second.commands == 2 * rounds


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["modern", "legacy"])
async def test_wire_cancellation_releases_pending_command_and_keeps_connection_usable(
    assurance_server, mode
):
    # Given: an explicitly cancellation-capable peer with one in-flight native command.
    with ControlledPlugin(assurance_server, 1).connected() as peer:
        async with wire_client(assurance_server, mode) as client:
            peer.hold_next = True
            cancelled = anyio.Event()

            async def hold() -> None:
                with anyio.CancelScope() as scope:
                    scopes.append(scope)
                    await read_scene(client, peer.instance)
                cancelled.set()

            scopes = []
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(hold)
                assert await anyio.to_thread.run_sync(lambda: peer.entered.wait(10))
                # When: cancellation reaches the official client's live tool request.
                scopes[0].cancel()
                with anyio.fail_after(10):
                    await cancelled.wait()
                assert await anyio.to_thread.run_sync(lambda: peer.cancelled.wait(10))
            # Then: the same client and plugin can complete another real command.
            assert await read_scene(client, peer.instance) == peer.hash
            retained = await state(client)
            assert retained["pending"] == retained["retained_results"] == 0
            assert retained["result_bytes"] == retained["raw_results"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["modern", "legacy"])
async def test_existing_client_routes_reconnected_plugin_generation(assurance_server, mode):
    # Given: a real client remains connected while its target's plugin socket is replaced.
    peer = ControlledPlugin(assurance_server, 1)
    async with wire_client(assurance_server, mode) as client:
        with peer.connected():
            assert await read_scene(client, peer.instance) == peer.hash
        # When: the same project registers on a new server-side plugin generation.
        with peer.connected():
            assert await read_scene(client, peer.instance) == peer.hash
            retained = await state(client)
            # Then: the existing client dispatches to the replacement and releases its result.
            assert retained["pending"] == retained["retained_results"] == 0
        assert peer.commands == 2


@pytest.mark.asyncio
async def test_official_probe_verifies_both_targets_and_owned_socket_cleanup(
    assurance_server, monkeypatch, tmp_path
):
    # Given: independent target identities and the same read-only probe used for native runs.
    root = Path(__file__).resolve().parents[3]
    monkeypatch.setenv("UNITY_MCP_LOCAL_AUTH_TOKEN", assurance_server[2])
    with (
        ControlledPlugin(assurance_server, 1).connected() as first,
        ControlledPlugin(assurance_server, 2).connected() as second,
    ):
        # When: the standalone official client reconnects over both protocol eras.
        result = await anyio.run_process(
            [
                sys.executable,
                str(root / "tools" / "experiments" / "mcp_client_assurance.py"),
                "--endpoint",
                f"{assurance_server[0]}/mcp",
                "--instance",
                first.instance,
                "--instance",
                second.instance,
                "--expected-scene",
                first.hash,
                "--expected-scene",
                second.hash,
                "--output",
                str(tmp_path / "receipt.json"),
            ],
            env={**os.environ, "PYTHONPATH": str(root / "Server" / "src")},
        )
        receipt = json.loads(result.stdout)
        assert receipt["connections"] == 4
        assert receipt["scene_reads"] == 8
        assert receipt["expected_scene_assertions"] is True
        assert {item["protocol"] for item in receipt["receipts"]} == {MODERN_VERSION, "2025-11-25"}
        assert first.commands == second.commands == 4
    # Then: peer ownership is physically released, including heartbeat and result state.
    async with wire_client(assurance_server, "modern") as client:
        with anyio.fail_after(10):
            while True:
                retained = await state(client)
                if retained["connections"] == retained["admitted"] == retained["ping_tasks"] == 0:
                    break
        assert retained["pending"] == retained["retained_results"] == retained["raw_results"] == 0


@contextmanager
def external_validator(tmp_path, monkeypatch) -> Iterator[str]:
    """Serve two synthetic verdicts over verified HTTPS without patching admission."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Assurance validator")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ip_address("127.0.0.1"))]), critical=False
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    certificate_path = tmp_path / "fixture-ca.pem"
    fixture_key = tmp_path / "fixture-private.pem"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    fixture_key.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    monkeypatch.setenv("SSL_CERT_FILE", str(certificate_path))

    class Validator(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            key = body.get("api_key")
            users = {"assurance-alice": "alice", "assurance-bob": "bob"}
            payload = json.dumps({"valid": key in users, "user_id": users.get(key)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format, *args) -> None:
            """Do not retain synthetic credentials or request logs."""

    with ThreadingHTTPServer(("127.0.0.1", 0), Validator) as validator:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate_path, fixture_key)
        validator.socket = context.wrap_socket(validator.socket, server_side=True)
        worker = Thread(target=validator.serve_forever, name="assurance-validator", daemon=True)
        worker.start()
        try:
            yield f"https://127.0.0.1:{validator.server_port}/validate"
        finally:
            validator.shutdown()
            worker.join(timeout=10)
            assert not worker.is_alive()
            fixture_key.unlink()
            certificate_path.unlink()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["modern", "legacy"])
async def test_production_auth_isolates_two_principals_and_denies_cross_target_dispatch(
    tmp_path, monkeypatch, mode
):
    # Given: the real remote admission stack calls a loopback external validator.
    with (
        external_validator(tmp_path, monkeypatch) as validation_url,
        local_server_process(
            tmp_path, module="tests.http.client_assurance_server", validation_url=validation_url
        ) as server,
        ControlledPlugin(server, 1, "assurance-alice").connected() as alice,
        ControlledPlugin(server, 2, "assurance-bob").connected() as bob,
    ):
        async with (
            wire_client(server, mode, "assurance-alice") as alice_client,
            wire_client(server, mode, "assurance-bob") as bob_client,
        ):
            for client, own, other in ((alice_client, alice, bob), (bob_client, bob, alice)):
                inventory = await client.read_resource(
                    "mcpforunity://instances", cache_mode="refresh"
                )
                instances = json.loads(inventory.contents[0].text)["instances"]
                assert {instance["id"] for instance in instances} == {own.instance}
                with anyio.fail_after(10):
                    while "manage_scene" not in {
                        tool.name for tool in (await client.list_tools(cache_mode="refresh")).tools
                    }:
                        await anyio.lowlevel.checkpoint()
                # When: the authenticated requester dispatches its own target and another owner's target.
                assert await read_scene(client, own.instance) == own.hash
                calls_before = other.commands
                try:
                    refused = await client.call_tool(
                        "manage_scene", {"action": "get_active", "unity_instance": other.instance}
                    )
                except MCPError:
                    pass  # Middleware rejection may use the protocol error boundary.
                else:
                    assert refused.is_error
                # Then: the unauthorized peer receives no command.
                assert other.commands == calls_before

            async def interleave(client: Client, peer: ControlledPlugin) -> None:
                for _ in range(8):
                    assert await read_scene(client, peer.instance) == peer.hash

            async with anyio.create_task_group() as tasks:
                tasks.start_soon(interleave, alice_client, alice)
                tasks.start_soon(interleave, bob_client, bob)


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["minimal", "core", "full"])
@pytest.mark.parametrize("transport", ["modern", "legacy", "stdio"])
async def test_profiles_large_catalog_and_legacy_reset_over_real_transports(
    tmp_path, monkeypatch, profile, transport
):
    # Given: actual production registration under each documented static profile.
    monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", profile)
    monkeypatch.setenv("UNITY_MCP_ASSURANCE_CATALOG_SIZE", str(CATALOG_SIZE))

    async def check(client: Client) -> None:
        initial = await client.list_tools(cache_mode="refresh")
        names = {tool.name for tool in initial.tools}
        generated = {name for name in names if name.startswith("assurance_catalog_")}
        assert len(generated) == (CATALOG_SIZE if profile == "full" else 0)
        assert "manage_tools" in names
        # When: the actual server describes a hidden tool, or legacy selection enables it.
        description = await client.call_tool(
            "manage_tools", {"action": "describe", "tool": "assurance_catalog_0000"}
        )
        assert not description.is_error
        assert description.structured_content["tool"]["input_schema"]["properties"]["value"]
        if transport != "modern":
            enabled = await client.call_tool("manage_tools", {"action": "activate", "group": "vfx"})
            assert enabled.structured_content["activated"] == "vfx"
            active = await client.list_tools(cache_mode="refresh")
            exposed = next(tool for tool in active.tools if tool.name == "assurance_catalog_0000")
            assert exposed.input_schema == description.structured_content["tool"]["input_schema"]
            assert (
                sum(tool.name.startswith("assurance_catalog_") for tool in active.tools)
                == CATALOG_SIZE
            )
            result = await client.call_tool("assurance_catalog_0000", {"value": 23})
            assert not result.is_error
            assert json.loads(result.content[0].text) == 23
            invalid = await client.call_tool("assurance_catalog_0000", {"value": []})
            assert invalid.is_error
            await client.call_tool("manage_tools", {"action": "reset"})
        # Then: reset/refresh returns the profile's complete original catalog.
        restored = await client.list_tools(cache_mode="refresh")
        assert {tool.name for tool in restored.tools} == names
        assert len({tool.name for tool in restored.tools}) == len(restored.tools)

    if transport == "stdio":
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "tests.http.client_assurance_server",
                "--transport",
                "stdio",
                "--project-scoped-tools",
            ],
            cwd=Path(__file__).resolve().parents[2],
            env={
                **os.environ,
                "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
                "DISABLE_TELEMETRY": "1",
                "UNITY_MCP_SKIP_STARTUP_CONNECT": "1",
                "UNITY_MCP_STATUS_DIR": str(tmp_path / "status"),
                "UNITY_MCP_LOG_DIR": str(tmp_path / "logs"),
            },
        )
        with (tmp_path / "stdio.log").open("w", encoding="utf-8") as log:
            async with Client(
                stdio_client(params, errlog=log), mode="legacy", cache=None
            ) as client:
                await check(client)
    else:
        with local_server_process(tmp_path, module="tests.http.client_assurance_server") as server:
            async with wire_client(server, transport) as client:
                await check(client)


@pytest.mark.asyncio
async def test_stdio_routes_two_framed_targets_and_restores_pinned_selection(tmp_path):
    # Given: isolated status files and real framed TCP peers with explicit legacy opt-in.
    root = Path(__file__).resolve().parents[2]
    with (
        LegacyPeer(tmp_path, 1).connected() as first,
        LegacyPeer(tmp_path, 2).connected() as second,
    ):
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "tests.http.client_assurance_server",
                "--transport",
                "stdio",
                "--project-scoped-tools",
            ],
            cwd=root,
            env={
                **os.environ,
                "PYTHONPATH": str(root / "src"),
                "DISABLE_TELEMETRY": "1",
                "UNITY_MCP_SKIP_STARTUP_CONNECT": "1",
                "UNITY_MCP_STATUS_DIR": str(tmp_path),
                "UNITY_MCP_STDIO_ALLOW_LEGACY": "1",
                "UNITY_MCP_TOOL_PROFILE": "full",
                "UNITY_MCP_LOG_DIR": str(tmp_path / "logs"),
            },
        )
        with (tmp_path / "stdio.log").open("w", encoding="utf-8") as log:
            async with Client(
                stdio_client(params, errlog=log), mode="legacy", cache=None
            ) as client:
                pin = await client.call_tool("set_active_instance", {"instance": first.instance})
                assert not pin.is_error
                # When: an explicit second-target call overrides the connection's pinned target.
                assert await read_scene(client, second.instance) == second.hash
                implicit = await client.call_tool("manage_scene", {"action": "get_active"})
                # Then: only that request changes route, and both sockets close with the child.
                assert implicit.structured_content["data"]["name"] == first.hash
                assert first.commands == second.commands == 1
