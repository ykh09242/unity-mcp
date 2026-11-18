"""Two independent MCP clients receive an observation over the same Editor transport."""

from copy import copy, deepcopy
import asyncio
import json
import time
from unittest.mock import AsyncMock

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError
import pytest

from services.resources import _serialize_pydantic
from services.resources.editor_state import get_editor_state
from services.resources.project_info import get_project_info
from services.tools.set_active_instance import set_active_instance
from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from transport.models import CommandResultMessage
from transport.plugin_hub import PluginHub
from utils.editor_handoff import receive_editor_handoff


def encoded_size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["stable", "scene", "reopened", "reused_handle", "legacy", "reconnect", "editor"]
)
async def test_receive_handoff_between_clients(command_hub, change):
    server = FastMCP("handoff-clients")
    server.tool(set_active_instance)
    server.resource("mcpforunity://project/info")(_serialize_pydantic(get_project_info))
    server.resource("mcpforunity://editor/state")(_serialize_pydantic(get_editor_state))
    instance = "Fixture@hash"
    scene = {"name": "Menu", "path": "Assets/Menu.unity", "guid": "menu-guid"}
    if change != "legacy":
        scene["handle"] = 31
        scene["lifetime_epoch"] = "1" * 32
    reads = []
    question = ["Observe the Game View dimensions"]
    native = {
        "observed_at_unix_ms": time.time_ns() // 1_000_000 - 100,
        "editor": {"active_scene": scene, "play_mode": {"is_playing": False, "is_changing": False}},
        "unity": {"instance_id": instance, "unity_version": "6000.0"},
    }

    def answer(socket):
        async def dispatch(message):
            reads.append(message["name"])
            if message["name"] == "get_project_info":
                data = {"projectName": instance.rpartition("@")[0], "unityVersion": "6000.0"}
            else:
                assert message["name"] == "get_editor_state"
                data = native
                native["observed_at_unix_ms"] = time.time_ns() // 1_000_000
            await command_hub.endpoint._handle_command_result(
                socket,
                CommandResultMessage(id=message["id"], result={"success": True, "data": data}),
            )

        return dispatch

    command_hub.websocket.send_json = answer(command_hub.websocket)
    params = {"instance": instance, "evidence_needed": question}
    async with (
        Client(server, mode="legacy", cache=False) as parent,
        Client(server, mode="legacy", cache=False) as child,
        Client(server, mode="legacy", cache=False) as independent,
    ):
        packet = (await parent.call_tool("set_active_instance", params)).structured_content["data"]
        previous = packet["handoff"]
        assert reads == ["get_editor_state"] and previous["unity_version"] == "6000.0"

        if change == "scene":
            scene.update(name="Boot", path="Assets/Boot.unity", guid="boot-guid")
        elif change == "reopened":
            scene["handle"] = 92
        elif change == "reused_handle":
            scene["lifetime_epoch"] = "2" * 32
        elif change in ("editor", "reconnect"):
            # Replace the registered socket. An Editor can reconnect with the same
            # public selector, while another target has its own name and hash.
            await command_hub.registry.unregister("session")
            old = PluginHub._connections.pop("session")
            socket = copy(old)
            socket.state = copy(old.state)
            socket.state.plugin_session_id = "replacement"
            socket.state.plugin_generation = "new-generation"
            if change == "editor":
                instance = "Another@elsewhere"
            name, _, project_hash = instance.rpartition("@")
            await command_hub.registry.register("replacement", name, project_hash, "6000.0")
            PluginHub._connections["replacement"] = socket
            socket.send_json = answer(socket)
            native["unity"]["instance_id"] = instance

        # Measure independent metadata and scene discovery on its own fresh client.
        project = (await independent.read_resource("mcpforunity://project/info"))[0].text
        state = (await independent.read_resource("mcpforunity://editor/state"))[0].text
        assert json.loads(project)["data"]["unityVersion"] == "6000.0"
        assert {
            k: v
            for k, v in json.loads(state)["data"]["editor"]["active_scene"].items()
            if v is not None
        } == scene
        assert reads[1:] == ["get_project_info", "get_editor_state"]
        cold_bytes = len(project.encode()) + len(state.encode())
        receipt_bytes = []

        async def checked_call(name, args):
            result = (await child.call_tool(name, args)).structured_content
            assert result["data"]["session_id"] != packet["session_id"]
            receipt_bytes.append(encoded_size(result))
            return result

        result = await receive_editor_handoff(checked_call, previous, instance=instance)
        assert (
            result["comparison"]
            == {
                "stable": "same_context",
                "scene": "scene_changed",
                "reopened": "scene_changed",
                "reused_handle": "scene_lifetime_unconfirmed",
                "legacy": "same_scene_asset",
                "editor": "instance_changed",
                "reconnect": "generation_changed",
            }[change]
        )
        assert result["evidence_needed"] == result["handoff"]["evidence_needed"] == question
        assert result["handoff"]["active_scene"] == scene
        assert result["handoff"]["instance"] == instance
        assert reads[3:] == ["get_editor_state"] and len(receipt_bytes) == 1
        if change == "stable":
            assert set(result["confirmed"]) == {
                "instance",
                "generation",
                "active_scene",
                "mode",
                "unity_version",
            }
            assert receipt_bytes[0] < cold_bytes
        elif change == "legacy":
            assert (
                result["confirmed"]["scene_asset"] == scene
                and "active_scene" not in result["confirmed"]
            )
        else:
            assert result["confirmed"] == {}
        print(
            f"{change}: independent=2 reads/{cold_bytes} bytes, receive=1 read/{receipt_bytes[0]} bytes, parent packet={encoded_size(previous)} bytes"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "old",
        "cached",
        "future",
        "same_timestamp",
        "unsaved",
        "transition",
        "denied",
        "unknown_generation",
    ],
)
async def test_uncertain_context_preserves_only_pending_questions(change):
    now = time.time_ns() // 1_000_000
    previous = {
        "instance": "Main@hash",
        "generation": "first",
        "source": "live_editor",
        "status": "observed",
        "mode": "edit",
        "active_scene": {"guid": "scene", "path": "Assets/Menu.unity", "name": "Menu"},
        "freshness": "point_in_time",
        "observed_at_unix_ms": now - 30000,
        "evidence_needed": ["Inspect Game View"],
    }
    current = deepcopy(previous)
    current["observed_at_unix_ms"] = now
    match change:
        case "old":
            current["observed_at_unix_ms"] = now - 10000
        case "cached":
            current["observed_at_unix_ms"] = now - 500
        case "future":
            current["observed_at_unix_ms"] = now + 10000
        case "same_timestamp":
            current["observed_at_unix_ms"] = previous["observed_at_unix_ms"]
        case "unsaved":
            current["active_scene"]["guid"] = previous["active_scene"]["guid"] = None
        case "transition":
            current["mode"] = "transitioning"
        case "unknown_generation":
            current["generation"] = None
    call = AsyncMock(return_value={"success": change != "denied", "data": {"handoff": current}})
    result = await receive_editor_handoff(call, previous)
    assert result["comparison"] != "same_context" and result["confirmed"] == {}
    assert result["evidence_needed"] == previous["evidence_needed"]
    call.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        "Remote operation permission denied",
        "explicit_unity_target_unavailable",
        "unrelated failure",
    ],
)
async def test_sdk_tool_refusal_preserves_checks_without_retry(message):
    server = FastMCP("handoff-refusal")
    calls = []

    @server.tool(name="set_active_instance")
    async def refuse(instance: str, include_context: bool, evidence_needed: list[str]):
        calls.append((instance, include_context, evidence_needed))
        raise ToolError(message)

    packet = {"instance": "Main@hash", "evidence_needed": ["Inspect Game View"]}
    async with Client(server, mode="legacy", cache=False) as child:
        if message == "unrelated failure":
            with pytest.raises(ToolError, match=message):
                await receive_editor_handoff(child.call_tool, packet)
        else:
            result = await receive_editor_handoff(child.call_tool, packet)
            assert result == {
                "comparison": "observation_unavailable",
                "confirmed": {},
                "handoff": {},
                "evidence_needed": packet["evidence_needed"],
            }
    assert calls == [(packet["instance"], True, packet["evidence_needed"])]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [MCPError(-32603, "Internal server error"), asyncio.CancelledError()]
)
async def test_unclassified_protocol_errors_and_cancellation_propagate(error):
    call = AsyncMock(side_effect=error)
    with pytest.raises(type(error)) as raised:
        await receive_editor_handoff(
            call, {"instance": "Main@hash", "evidence_needed": ["Inspect Game View"]}
        )
    assert raised.value is error
    call.assert_awaited_once()
