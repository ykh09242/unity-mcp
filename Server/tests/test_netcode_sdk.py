"""Netcode diagnostics through the real FastMCP schema and transport boundary."""

from importlib import import_module

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
import pytest

from core.logging_decorator import log_execution
from core.telemetry_decorator import telemetry_tool
from services.registry import get_registered_tools


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_netcode_preserves_scope_settings_and_native_failure(monkeypatch):
    # Given a real SDK tool and a captured selected-instance transport.
    module = import_module("services.tools.manage_netcode")
    sent = []
    reply = {"success": False, "error": "netcode_package_unavailable", "data": {"ready": False}}

    async def selected(_ctx):
        return "OwnedProject@ngo"

    async def send(_send, instance, command, params):
        sent.append((instance, command, params))
        return reply

    monkeypatch.setattr(module, "get_unity_instance_from_context", selected)
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    server = FastMCP("netcode-contract")
    wrapped = log_execution("manage_netcode", "Tool")(module.manage_netcode)
    metadata = next(t for t in get_registered_tools() if t["name"] == "manage_netcode")
    assert metadata["group"] == "core"
    assert metadata["unity_target"] == "manage_netcode"
    server.tool(name="manage_netcode", **metadata["kwargs"])(
        telemetry_tool("manage_netcode")(wrapped)
    )

    # When both supported SDK modes invoke the typed tool.
    for mode in ("2026-07-28", "legacy"):
        async with Client(server, mode=mode) as client:
            tool = next(t for t in await client.list_tools() if t.name == "manage_netcode")
            assert tool.input_schema["required"] == ["action"]
            assert tool.annotations.read_only_hint is True
            assert tool.annotations.destructive_hint is False
            arguments = {
                "action": "preflight",
                "scene_handle": -71,
                "max_prefabs": 5,
                "expected_settings": {
                    "ProtocolVersion": 0,
                    "TickRate": 30,
                    "EnableSceneManagement": False,
                    "ForceSamePrefabs": False,
                    "ConnectionApproval": True,
                },
            }
            result = await client.call_tool("manage_netcode", arguments)
            # Then exact identities, zero/false values, and the native refusal survive.
            assert sent[-1] == ("OwnedProject@ngo", "manage_netcode", arguments)
            assert result.structured_content == reply


@pytest.mark.anyio
async def test_netcode_schema_refuses_invalid_input_before_transport(monkeypatch):
    # Given the actual schema with a transport that must remain unused.
    module = import_module("services.tools.manage_netcode")
    calls = []

    async def send(*args):
        calls.append(args)
        return {"success": True}

    monkeypatch.setattr(module, "send_with_unity_instance", send)
    server = FastMCP("netcode-invalid")
    server.tool(name="manage_netcode")(module.manage_netcode)
    invalid = [
        {"action": "start_host"},
        {"action": "preflight", "scene_handle": True},
        {"action": "preflight", "scene_handle": 1.5},
        {"action": "preflight", "scene_handle": "71"},
        {"action": "preflight", "scene_handle": 71, "max_prefabs": 0},
        {"action": "preflight", "scene_handle": 71, "max_prefabs": 1001},
        {"action": "preflight", "scene_handle": 71, "max_prefabs": True},
        {"action": "preflight", "scene_handle": 71, "expected_settings": {"TickRate": 0}},
        {"action": "preflight", "scene_handle": 71, "expected_settings": {"TickRate": "30"}},
        {"action": "preflight", "scene_handle": 71, "expected_settings": {"TickRate": True}},
        {"action": "preflight", "scene_handle": 71, "expected_settings": {"ProtocolVersion": -1}},
        {
            "action": "preflight",
            "scene_handle": 71,
            "expected_settings": {"ProtocolVersion": 65536},
        },
        {"action": "preflight", "scene_handle": 71, "expected_settings": {"ConnectionApproval": 0}},
        {
            "action": "preflight",
            "scene_handle": 71,
            "expected_settings": {"ConnectionData": "secret"},
        },
    ]
    # When malformed or mutating requests cross the real SDK boundary.
    async with Client(server) as client:
        for arguments in invalid:
            with pytest.raises(ToolError):
                await client.call_tool("manage_netcode", arguments)
        result = await client.call_tool("manage_netcode", {"action": "preflight"})
    # Then the SDK or wrapper rejects every request before any native call.
    assert result.structured_content["success"] is False
    assert calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("reply", [None, [], "invalid envelope"])
async def test_netcode_capabilities_fail_closed_on_malformed_transport(monkeypatch, reply):
    # Given a malformed transport response.
    module = import_module("services.tools.manage_netcode")
    captured = []

    async def selected(_ctx):
        return None

    async def send(_send, instance, command, params):
        captured.append((instance, command, params))
        return reply

    monkeypatch.setattr(module, "get_unity_instance_from_context", selected)
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    server = FastMCP("netcode-malformed")
    server.tool(name="manage_netcode")(module.manage_netcode)
    # When capabilities are requested without an implicit scene scope.
    async with Client(server) as client:
        result = await client.call_tool("manage_netcode", {"action": "capabilities"})
    # Then only capabilities are sent and the malformed response is a failure.
    assert captured == [(None, "manage_netcode", {"action": "capabilities"})]
    assert result.structured_content == {"success": False, "message": str(reply)}
