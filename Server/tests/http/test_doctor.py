"""Doctor receipts require current native evidence and comparable source identities."""

import json
from types import SimpleNamespace

import anyio
from fastmcp import Client
import pytest

from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from tests.http.test_project_capabilities import (
    capability_server,
    install_native_replies,
    native_observations,
    read_capabilities,
)

COMMIT = "a" * 40


def doctor_server():
    from services.resources import _serialize_pydantic
    from services.resources.doctor import get_doctor

    server = capability_server()
    server.resource("mcpforunity://doctor")(_serialize_pydantic(get_doctor))
    return server


def observations():
    result = native_observations()
    result["get_project_info"].update(
        unityVersion="6000.0.69f1",
        mcpPackage={
            "name": "com.ykh09242.unity-mcp",
            "version": "1.2.1",
            "packageServerCommit": COMMIT,
            "effectiveSourceKind": "package_pin",
            "effectiveServerCommit": COMMIT,
            "skillRevision": "b" * 40,
        },
    )
    return result


async def read_doctor(client):
    blocks = await client.read_resource("mcpforunity://doctor")
    return json.loads(blocks[0].text)["data"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_public_sdk_discovers_doctor_and_verifies_native_read(command_hub, monkeypatch, mode):
    from core.server_build import ServerBuild
    import services.doctor as doctor

    monkeypatch.setattr(doctor, "RUNNING_SERVER", ServerBuild("1.2.0", COMMIT, "c" * 32))
    await install_native_replies(command_hub, observations())
    async with Client(doctor_server(), mode=mode, cache=False) as client:
        assert "mcpforunity://doctor" in [str(item.uri) for item in await client.list_resources()]
        await client.call_tool("select_fixture")
        data = await read_doctor(client)
    assert data["schema_version"] == "unity-mcp/doctor@1"
    assert data["scope"] == "current_mcp_request"
    assert data["connection_verified"] is True
    assert data["source_comparison"] == "matched"
    assert data["package"]["version"] == "1.2.1"
    assert data["server"]["version"] == "1.2.0"
    assert data["package"]["skill_revision"] == "b" * 40
    assert len([m for m in command_hub.sent if m["type"] == "execute"]) == 5
    assert data["residual_server_work"] == "unknown"
    assert len(json.dumps(data).encode()) < 32768


@pytest.mark.asyncio
@pytest.mark.parametrize("commit,comparison", [(None, "unknown"), ("d" * 40, "mismatch")])
async def test_same_semver_does_not_replace_source_comparison(
    command_hub, monkeypatch, commit, comparison
):
    from core.server_build import ServerBuild
    import services.doctor as doctor

    monkeypatch.setattr(doctor, "RUNNING_SERVER", ServerBuild("1.2.1", commit, "c" * 32))
    await install_native_replies(command_hub, observations())
    async with Client(doctor_server(), mode="legacy") as client:
        await client.call_tool("select_fixture")
        data = await read_doctor(client)
    assert data["connection_verified"] is True
    assert data["source_comparison"] == comparison


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_idle_editor_with_native_recovery_never_passes_readiness(command_hub, mode):
    replies = observations()
    replies["get_editor_state"]["native_coordination"] = {
        "blocked": True,
        "reason": "recovery_required",
        "retryable": False,
        "recovery_action": "inspect_native_work_then_restart_editor",
    }
    await install_native_replies(command_hub, replies)
    async with Client(doctor_server(), mode=mode, cache=False) as client:
        await client.call_tool("select_fixture")
        data = await read_doctor(client)
        capabilities = await read_capabilities(client)
    assert data["connection_verified"] is True
    check = data["checks"]["readiness"]
    assert check["status"] == "not_checked"
    assert check["reason"] == "recovery_required"
    assert check["next_action"] == "inspect_native_work"
    assert check["native_coordination"]["reason"] == "recovery_required"
    assert capabilities["readiness"]["status"] == "blocked"
    assert "recovery_required" in capabilities["readiness"]["blocking_reasons"]
    assert capabilities["readiness"]["next_action"] == "inspect_native_work"


@pytest.mark.asyncio
async def test_ambiguous_source_failure_remains_unknown_without_raw_error(command_hub):
    await install_native_replies(command_hub, {})
    async with Client(doctor_server(), mode="legacy") as client:
        await client.call_tool("select_fixture")
        data = await read_doctor(client)
    assert data["connection_verified"] is False
    assert data["checks"]["native_read"]["status"] == "unknown"
    assert data["checks"]["native_read"]["reason"] == "source_unavailable"


@pytest.mark.asyncio
async def test_final_identity_change_discards_package_and_read_proof(command_hub):
    await install_native_replies(command_hub, observations())
    reply = command_hub.websocket.send_json

    async def replace_generation(message):
        await reply(message)
        command_hub.websocket.state.plugin_generation = "replaced"

    command_hub.websocket.send_json = replace_generation
    async with Client(doctor_server(), mode="legacy") as client:
        await client.call_tool("select_fixture")
        data = await read_doctor(client)
    assert data["connection_verified"] is False
    assert data["target"]["instance_id"] is None
    assert data["package"]["version"] is None
    assert data["source_comparison"] == "unknown"


@pytest.mark.asyncio
async def test_capabilities_wire_contract_unchanged_by_doctor_view(command_hub):
    await install_native_replies(command_hub, observations())
    async with Client(doctor_server(), mode="legacy") as client:
        await client.call_tool("select_fixture")
        data = await read_capabilities(client)
    assert set(data) == {
        "schema_version",
        "cache_ttl_seconds",
        "partial",
        "target",
        "connection",
        "limits",
        "readiness",
        "packages",
        "features",
        "evidence",
    }
    assert data["schema_version"] == "unity-mcp/capabilities@1"
    assert len([m for m in command_hub.sent if m["type"] == "execute"]) == 5


@pytest.mark.asyncio
async def test_doctor_registry_wait_uses_original_capability_budget(command_hub, monkeypatch):
    import services.capabilities as capabilities

    monkeypatch.setattr(capabilities, "_QUERY_TIMEOUT_SECONDS", 0.05)
    async with Client(doctor_server(), mode="legacy") as client:
        await client.call_tool("select_fixture")
        async with command_hub.registry._lock:
            started = anyio.current_time()
            data = await read_doctor(client)
            elapsed = anyio.current_time() - started
    assert elapsed < 0.3
    assert command_hub.sent == []
    assert data["connection_verified"] is False
    assert data["package"]["version"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", ["credential-bearing-url", "x" * 400, {"nested": "secret"}])
async def test_native_identity_fields_are_allowlisted(command_hub, payload):
    values = observations()
    values["get_project_info"]["mcpPackage"].update(
        version=payload, source_url="https://user:secret@host"
    )
    await install_native_replies(command_hub, values)
    async with Client(doctor_server(), mode="legacy") as client:
        await client.call_tool("select_fixture")
        data = await read_doctor(client)
    wire = json.dumps(data)
    assert "https://user:secret@host" not in wire
    assert "source_url" not in wire
    assert data["package"]["version"] is None


@pytest.mark.asyncio
async def test_cold_stdio_direct_read_does_not_authenticate_or_dispatch(monkeypatch):
    from core.config import config
    import services.capabilities as capabilities
    from services.doctor import query_doctor

    monkeypatch.setattr(config, "transport_mode", "stdio")
    generations = []

    async def no_generation(instance):
        generations.append(instance)
        return None

    async def state(key):
        return "Fixture@hash" if key == "unity_instance" else None

    monkeypatch.setattr(capabilities, "get_authenticated_stdio_generation", no_generation)
    result = await query_doctor(SimpleNamespace(get_state=state, request_context=None))
    assert result.connection_verified is False
    assert result.checks["native_read"].status == "unknown"
    assert result.package.version is None
    assert generations == ["Fixture@hash"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("greeting", ["v1", "v2"])
async def test_real_cold_stdio_discovery_ping_does_not_create_command_generation(
    monkeypatch, tmp_path, mode, greeting
):
    import asyncio  # noqa: ANYIO_OK -- run the real framed discovery listener.
    import struct
    from core.config import config
    from transport.plugin_hub import PluginHub
    import transport.legacy.unity_connection as legacy
    from tests.timing import await_completion

    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(PluginHub, "_registry", None)
    monkeypatch.setenv("UNITY_MCP_STATUS_DIR", str(tmp_path))
    monkeypatch.delenv("UNITY_MCP_DEFAULT_INSTANCE", raising=False)
    pool = legacy.UnityConnectionPool()
    monkeypatch.setattr(legacy, "_unity_connection_pool", pool)
    commands = []
    authentications = []
    probes = []
    probe_handlers = []

    def connect(*args, **kwargs):
        authentications.append(True)
        pytest.fail("cold doctor created an authenticated command connection")

    def command(*args, **kwargs):
        commands.append(True)
        pytest.fail("cold doctor sent a native command")

    monkeypatch.setattr(legacy.UnityConnection, "connect", connect)
    monkeypatch.setattr(legacy.UnityConnection, "send_command", command)

    async def listener(reader, writer):
        probe_handlers.append(asyncio.current_task())
        try:
            banner = (
                b"WELCOME UNITY-MCP 1 FRAMING=1\n"
                if greeting == "v1"
                else b"WELCOME UNITY-MCP 2 FRAMING=1 AUTH=HMAC-SHA256 SERVER="
                + b"a" * 32
                + b" CHALLENGE="
                + b"b" * 64
                + b"\n"
            )
            writer.write(banner)
            await writer.drain()
            if greeting == "v2":
                # Discovery closes the unauthenticated socket after its greeting.
                # Any authentication or business frame would replace/command a peer.
                probes.append(await reader.read())
                return
            length = struct.unpack(">Q", await reader.readexactly(8))[0]
            payload = await reader.readexactly(length)
            probes.append(payload)
            reply = b'{"message":"pong"}'
            writer.write(struct.pack(">Q", len(reply)) + reply)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async with await asyncio.start_server(listener, "127.0.0.1", 0) as tcp:
        port = tcp.sockets[0].getsockname()[1]
        (tmp_path / "unity-mcp-status-aaaa1111.json").write_text(
            json.dumps({"project_path": "/Owned/Fixture/Assets", "unity_port": port}),
            encoding="utf-8",
        )
        async with Client(doctor_server(), mode=mode, cache=False) as client:
            assert "mcpforunity://doctor" in [
                str(item.uri) for item in await client.list_resources()
            ]
            blocks = await client.read_resource(
                "mcpforunity://doctor", meta={"unity_instance": "Fixture@aaaa1111"}
            )
            data = json.loads(blocks[0].text)["data"]
        await await_completion(asyncio.gather(*probe_handlers), "doctor discovery peers")
    assert probes and all(probe == (b"ping" if greeting == "v1" else b"") for probe in probes)
    assert authentications == commands == []
    assert pool._connections == {}
    assert data["connection_verified"] is False
    assert data["checks"]["native_read"]["status"] == "unknown"
    assert data["residual_server_work"] == "unknown"


@pytest.mark.asyncio
async def test_conflicting_native_target_clears_package_and_fails_read_proof(command_hub):
    values = observations()
    values["get_editor_state"]["unity"]["instance_id"] = "Other@bbbb2222"
    await install_native_replies(command_hub, values)
    async with Client(doctor_server(), mode="legacy") as client:
        await client.call_tool("select_fixture")
        data = await read_doctor(client)
    assert data["connection_verified"] is False
    assert data["checks"]["target"]["reason"] == "wrong_target"
    assert data["checks"]["target"]["status"] == "failed"
    assert data["package"]["version"] is None
    assert data["editor_version"] is None


@pytest.mark.asyncio
async def test_capacity_refusal_dispatches_zero_commands_and_stays_unknown(
    command_hub, monkeypatch
):
    from transport.plugin_hub import PluginHub

    monkeypatch.setattr(PluginHub, "MAX_PENDING_COMMANDS", 0)
    async with Client(doctor_server(), mode="legacy") as client:
        await client.call_tool("select_fixture")
        data = await read_doctor(client)
    assert command_hub.sent == []
    assert data["checks"]["native_read"]["reason"] == "capacity"
    assert data["checks"]["native_read"]["status"] == "unknown"
    assert data["connection_verified"] is False


@pytest.mark.asyncio
async def test_malformed_installed_package_version_cannot_prove_source_match(
    command_hub, monkeypatch
):
    from core.server_build import ServerBuild
    import services.doctor as doctor

    monkeypatch.setattr(doctor, "RUNNING_SERVER", ServerBuild("1.2.0", COMMIT, "c" * 32))
    values = observations()
    values["get_project_info"]["mcpPackage"]["version"] = "https://user:secret@host"
    await install_native_replies(command_hub, values)
    async with Client(doctor_server(), mode="legacy") as client:
        await client.call_tool("select_fixture")
        data = await read_doctor(client)
    assert data["connection_verified"] is True
    assert data["source_comparison"] == "unknown"
    assert data["package"]["effective_server_commit"] is None
