"""Current-socket observations never cross tenants, reconnects or the read deadline."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anyio
from fastmcp import Client
import pytest
from starlette.websockets import WebSocketState

from core.config import config
from models.models import ToolDefinitionModel
from services.custom_tool_service import CustomToolService
from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from tests.http.test_http_request_deadline import request_server
from tests.http.test_project_capabilities import install_native_replies
from transport.models import RegisterToolsMessage
from transport.plugin_hub import PluginHub


def diagnostics_server(monkeypatch):
    from services.resources import _serialize_pydantic
    from services.resources.tool_diagnostics import get_tool_diagnostics, get_named_tool_diagnostics

    server = request_server()
    monkeypatch.setattr(CustomToolService, "_instance", None)
    service = CustomToolService(server)
    monkeypatch.setattr(PluginHub, "_mcp", server)
    server.resource("mcpforunity://tool-diagnostics")(_serialize_pydantic(get_tool_diagnostics))
    server.resource("mcpforunity://tool-diagnostics/{tool_name}")(
        _serialize_pydantic(get_named_tool_diagnostics)
    )
    return server, service


def native_data(name="owned"):
    return {
        "schema_version": "unity-mcp/native-tool-diagnostics@1",
        "complete": True,
        "rows": [
            {
                "tool_name": name,
                "stage": "native_enablement",
                "state": "disabled",
                "reason": "native_disabled",
                "next_action": "review_tool_toggle",
                "native_enabled": False,
            }
        ],
    }


async def read(client, name=None):
    from urllib.parse import quote

    uri = "mcpforunity://tool-diagnostics" + ("/" + quote(name, safe="") if name else "")
    return json.loads((await client.read_resource(uri))[0].text)["data"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_public_named_read_uses_one_nonmutating_probe_and_current_catalog(
    command_hub, monkeypatch, mode
):
    server, service = diagnostics_server(monkeypatch)
    await command_hub.endpoint._handle_register_tools(
        command_hub.websocket, RegisterToolsMessage(tools=[ToolDefinitionModel(name="owned")])
    )
    old = service._sources[("http", None, "hash")]
    notify = AsyncMock()
    monkeypatch.setattr(PluginHub, "_notify_mcp_tool_list_changed", notify)
    await install_native_replies(command_hub, {"get_tool_states": native_data()})
    async with Client(server, mode=mode) as client:
        await client.call_tool("select_fixture")
        data = await read(client, "owned")
    assert data["native"]["rows"][0]["native_enabled"] is False
    assert data["server"][0]["rows"][0]["state"] == "registered"
    assert service._sources[old.key] is old
    assert notify.await_count == 0
    commands = [message for message in command_hub.sent if message["type"] == "execute"]
    assert len(commands) == 1 and commands[0]["name"] == "get_tool_states"
    assert commands[0]["params"]["diagnostics"] is True


@pytest.mark.asyncio
async def test_real_named_unicode_template_decodes_once(command_hub, monkeypatch):
    server, _ = diagnostics_server(monkeypatch)
    name = "owned_\u03bb"
    await install_native_replies(command_hub, {"get_tool_states": native_data(name)})
    async with Client(server, mode="legacy") as client:
        data = await read(client, name)
    assert data["native"]["rows"][0]["tool_name"] == name


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
@pytest.mark.parametrize("named", [True, False])
async def test_public_row_limit_is_aggregate_across_native_and_both_server_sources(
    command_hub, monkeypatch, mode, named
):
    server, service = diagnostics_server(monkeypatch)
    size = 16 if named else 64
    names = ["owned" if named else f"owned_{i}" for i in range(size)]
    service.record_diagnostic_attempt("http", "hash", "session", names=names)
    service.record_diagnostic_attempt("manual", "hash", names=names)
    native = native_data()
    native["rows"] = [dict(native["rows"][0], tool_name=name) for name in names]
    await install_native_replies(command_hub, {"get_tool_states": native})
    async with Client(server, mode=mode) as client:
        await client.call_tool("select_fixture")
        data = await read(client, "owned" if named else None)
    groups = [data["native"], *data["server"]]
    assert len(groups) == 3
    assert sum(len(group["rows"]) for group in groups) == size
    assert all(group["rows"] and group["truncated"] for group in groups)
    assert data["partial"] is True
    assert data["limits"]["row_accounting"] == "aggregate_response"


@pytest.mark.asyncio
async def test_stored_disconnected_selection_spends_original_read_budget(command_hub, monkeypatch):
    import services.capabilities as capabilities

    server, _ = diagnostics_server(monkeypatch)
    monkeypatch.setattr(capabilities, "_QUERY_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setenv("UNITY_MCP_SESSION_RESOLVE_MAX_WAIT_S", "1")
    async with Client(server, mode="legacy") as client:
        await client.call_tool("select_fixture")
        await command_hub.registry.unregister("session")
        started = anyio.current_time()
        data = await read(client)
    assert anyio.current_time() - started < 0.25
    assert not command_hub.sent and data["server"] == []
    assert data["connection"]["instance_id"] is None


@pytest.mark.asyncio
async def test_reconnect_during_reply_discards_all_attributed_observations(
    command_hub, monkeypatch
):
    server, _ = diagnostics_server(monkeypatch)
    await install_native_replies(command_hub, {"get_tool_states": native_data()})
    original = command_hub.websocket.send_json

    async def reply_then_reconnect(message):
        await original(message)
        if message["type"] == "execute":
            command_hub.websocket.state.plugin_generation = "replacement"

    command_hub.websocket.send_json = reply_then_reconnect
    async with Client(server, mode="legacy") as client:
        data = await read(client)
    assert data["server"] == [] and data["native"]["rows"] == []
    assert data["connection"]["generation"] is None


@pytest.mark.asyncio
async def test_blocked_cancel_and_revalidation_share_one_cleanup_grace(command_hub, monkeypatch):
    import services.capabilities as capabilities

    server, _ = diagnostics_server(monkeypatch)
    monkeypatch.setattr(capabilities, "_QUERY_TIMEOUT_SECONDS", 0.06)
    monkeypatch.setattr(PluginHub, "CANCEL_TIMEOUT", 0.08)

    async def blocked(message):
        command_hub.sent.append(message)
        if message["type"] == "cancel":
            await command_hub.registry._lock.acquire()
        await anyio.sleep_forever()

    command_hub.websocket.send_json = blocked
    try:
        async with Client(server, mode="legacy") as client:
            started = anyio.current_time()
            data = await read(client)
            elapsed = anyio.current_time() - started
    finally:
        if command_hub.registry._lock.locked():
            command_hub.registry._lock.release()
    assert elapsed < data["query_max_elapsed_seconds"] + 0.15
    assert data["native"]["rows"] == data["server"] == []
    assert data["connection"]["instance_id"] is None
    assert PluginHub._pending == {}


@pytest.mark.asyncio
async def test_old_socket_failure_and_retirement_cannot_touch_replacement(command_hub, monkeypatch):
    server, service = diagnostics_server(monkeypatch)
    old_socket = command_hub.websocket
    old_session = "session"
    await command_hub.registry.register("replacement", "Fixture", "hash", "6000")
    current_socket = SimpleNamespace(
        state=SimpleNamespace(
            user_id=None, plugin_session_id="replacement", plugin_registered=True
        ),
        client_state=WebSocketState.CONNECTED,
        application_state=WebSocketState.CONNECTED,
    )
    PluginHub._connections["replacement"] = current_socket
    service.record_diagnostic_attempt("http", "hash", "replacement", names=["new_owned"])
    replacement = service.get_diagnostic_attempts("http", "hash", generation="replacement")
    await PluginHub._record_tool_diagnostic_failure(old_socket, "metadata_invalid", ["stale"])
    service.retire_diagnostic_generation("http", "hash", old_session)
    assert service.get_diagnostic_attempts("http", "hash", generation="replacement") is replacement
    assert service.get_diagnostic_attempts("http", "hash", generation=old_session) is None


@pytest.mark.asyncio
async def test_hosted_scope_is_current_tenant_not_payload_principal(command_hub, monkeypatch):
    _, service = diagnostics_server(monkeypatch)
    monkeypatch.setattr(config, "http_remote_hosted", True)
    for tenant in ("a", "b"):
        sid = "session-" + tenant
        await command_hub.registry.register(sid, "Fixture", "hash", "6000", user_id=tenant)
        socket = SimpleNamespace(
            state=SimpleNamespace(user_id=tenant, plugin_registered=True, plugin_session_id=sid),
            client_state=WebSocketState.CONNECTED,
            application_state=WebSocketState.CONNECTED,
        )
        PluginHub._connections[sid] = socket
        await PluginHub._record_tool_diagnostic_failure(
            socket, "metadata_invalid", ["owned_" + tenant]
        )
    rows = await PluginHub.get_tool_diagnostic_attempts("Fixture@hash", "a")
    assert [row.tool_name for row in rows[0].rows] == ["owned_a"]
    assert await PluginHub.get_tool_diagnostic_attempts("Fixture@hash", None) == []
    assert "owned_b" not in rows[0].model_dump_json()


@pytest.mark.asyncio
async def test_private_order_fence_preserves_newer_precise_failure_after_foreign_activity(
    command_hub, monkeypatch
):
    _, service = diagnostics_server(monkeypatch)
    service.record_diagnostic_attempt("http", "hash", "session", names=["owned"])
    for _ in range(7):
        service.record_diagnostic_attempt("http", "foreign", "other", names=["foreign"])
    captured = service._diagnostic_sequence
    service.record_diagnostic_attempt(
        "http", "hash", "session", names=["owned"], reason="schema_or_default_invalid"
    )
    precise = service.get_diagnostic_attempts("http", "hash", generation="session")
    assert precise.attempt == 2
    await PluginHub._record_tool_diagnostic_failure(
        command_hub.websocket, "publication_failed", ["owned"], since_sequence=captured
    )
    assert service.get_diagnostic_attempts("http", "hash", generation="session") is precise


@pytest.mark.asyncio
async def test_malformed_frame_records_safe_failure_and_provisional_is_unobserved(
    command_hub, monkeypatch
):
    _, service = diagnostics_server(monkeypatch)
    await command_hub.endpoint._handle_register_tools(
        command_hub.websocket, RegisterToolsMessage(tools=[ToolDefinitionModel(name="prior")])
    )
    previous = service._sources[("http", None, "hash")]
    with pytest.raises(ValueError):
        await command_hub.endpoint._handle_register_tools_frame(
            command_hub.websocket,
            {
                "type": "register_tools",
                "tools": [{"name": "broken", "parameters": ["secret payload"]}],
            },
        )
    attempt = service.get_diagnostic_attempts("http", "hash", generation="session")
    assert attempt.rows[0].reason == "metadata_invalid"
    assert "secret" not in attempt.model_dump_json()
    assert service._sources[previous.key] is previous
    service.replace_project_catalog("hash", [], generation="new-provisional", known=False)
    assert service.get_diagnostic_attempts("http", "hash", generation="new-provisional") is None
    assert service.get_diagnostic_attempts("http", "hash", generation="session") is None


@pytest.mark.asyncio
async def test_public_limits_distinguish_encoded_bytes_from_object_caps(command_hub, monkeypatch):
    server, _ = diagnostics_server(monkeypatch)
    await install_native_replies(command_hub, {"get_tool_states": native_data()})
    async with Client(server, mode="legacy") as client:
        data = await read(client)
    limits = data["limits"]
    assert limits["byte_accounting"] == "utf8_json_including_scope_metadata"
    assert limits["server_scope_encoded_bytes"] == 32 * 1024
    assert limits["server_total_encoded_bytes"] == 512 * 1024
    assert limits["native_encoded_bytes"] == 128 * 1024
    assert limits["source_scopes"] == 256 and limits["native_retained_rows"] == 512
    assert limits["named_rows"] == 16 and limits["preview_rows"] == 64
