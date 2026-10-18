"""Proposed installed-SDK boundary; missing-action baseline and all runs are UNRUN."""

import copy
import importlib

import anyio
from fastmcp import Client, FastMCP
from fastmcp.exceptions import MCPError

from .test_ugui_raycast_diagnostics import opted_in_failure, sample


def test_actual_sdk_modes_require_new_action_false_default_and_closed_read_dispatch(monkeypatch):
    # Given: actual SDK publication and a controlled fake native read, never Unity/network.
    module = importlib.import_module("services.tools.manage_ugui")
    registry = importlib.import_module("services.registry")
    calls = []
    reply = sample()

    async def preflight(*_args, **_kwargs):
        return None

    async def instance(*_args, **_kwargs):
        return None

    async def read(*args, **_kwargs):
        calls.append(args[3])
        return copy.deepcopy(reply)

    async def mutation(*_args, **_kwargs):
        raise AssertionError("Raycast evidence used mutation transport")

    monkeypatch.setattr(module, "preflight", preflight)
    monkeypatch.setattr(module, "get_unity_instance_from_context", instance)
    monkeypatch.setattr(module, "send_with_unity_instance", read)
    monkeypatch.setattr(module, "send_mutation", mutation)
    entry = next(item for item in registry.get_registered_tools() if item["name"] == "manage_ugui")
    server = FastMCP("ui09-proposed-publication")
    server.tool(name="manage_ugui", description=entry["description"], **entry["kwargs"])(
        module.manage_ugui
    )

    async def scenario():
        for mode in ("2026-07-28", "legacy"):
            async with Client(server, mode=mode) as client:
                tool = next(
                    tool for tool in await client.list_tools() if tool.name == "manage_ugui"
                )
                schema = tool.input_schema if hasattr(tool, "input_schema") else tool.inputSchema
                # This is the intended missing-action baseline assertion, not an executed RED.
                assert "diagnose_raycast" in schema["properties"]["action"]["enum"]
                assert schema["properties"]["allow_custom_filters"]["default"] is False
                options = {
                    "action": "diagnose_raycast",
                    "target": 7,
                    "screen_position": [100.0, 80.0],
                }
                # When: a current-point diagnostic is requested with no opt-in.
                result = await client.call_tool("manage_ugui", options)
                # Then: use read transport, preserve the full native envelope and default false.
                assert result.structured_content == reply
                assert calls[-1]["allow_custom_filters"] is False
                assert calls[-1]["screen_position"] == [100.0, 80.0]
                for invalid in (
                    {"allow_custom_filters": "yes"},
                    {"screen_position": [1]},
                    {"max_evaluations": 0},
                    {"max_nodes": True},
                    {"properties": {"text": "PRIVATE_SENTINEL"}},
                    {"resolutions": [{"width": 800, "height": 600}]},
                ):
                    before = len(calls)
                    try:
                        invalid_result = await client.call_tool(
                            "manage_ugui", {**options, **invalid}, raise_on_error=False
                        )
                    except MCPError:
                        invalid_result = None
                    if invalid_result is not None:
                        assert (
                            invalid_result.is_error
                            or invalid_result.structured_content["success"] is False
                        )
                    assert len(calls) == before
                reply.clear()
                reply.update(opted_in_failure())
                result = await client.call_tool(
                    "manage_ugui", {**options, "allow_custom_filters": True}
                )
                assert result.structured_content == reply
                assert result.structured_content["data"]["policy"]["user_code_may_have_run"] is True
                reply.clear()
                reply.update(sample())
                server.disable(tags={"group:ui"}, components={"tool"})
                assert "manage_ugui" not in {tool.name for tool in await client.list_tools()}
                server.enable(tags={"group:ui"}, components={"tool"})

    anyio.run(scenario)
