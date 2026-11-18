"""Proposed installed-SDK boundary; missing-action baseline and all runs are UNRUN."""

import copy
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anyio
from fastmcp import Client, Context, FastMCP
from fastmcp.exceptions import MCPError
import pytest

from core.config import config
from services.resources import _serialize_pydantic
from transport.plugin_hub import PluginHub
from transport.unity_instance_middleware import UnityInstanceMiddleware

from .test_ugui_raycast_diagnostics import opted_in_failure, sample


def _unknown_reply(reason, culprit=None):
    reply = sample()
    reply["data"].update(
        status="unknown",
        reason=reason,
        hits=[],
        hits_total=None,
        first_hit_targets_handler=None,
    )
    reply["data"]["policy"].update(custom_filters_present=None, native_query_attempted=False)
    if culprit is None:
        reply["data"]["input"]["event_system_id"] = None
    else:
        reply["data"]["unsupported_component"] = culprit
        reply["data"]["budget"]["characters_reserved"] = 512
    return reply


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
                for reason, culprit, expected in (
                    (
                        "missing_event_system",
                        None,
                        {
                            "basis": "event_system_candidates",
                            "retry_unchanged": False,
                            "tool": "find_gameobjects",
                            "arguments": {
                                "search_method": "by_component",
                                "search_term": "UnityEngine.EventSystems.EventSystem",
                                "scene_scope": "all_loaded",
                                "include_inactive": True,
                                "page_size": 20,
                                "max_depth": 64,
                                "projection": ["name", "active", "components"],
                                "refresh_if_dirty": False,
                            },
                        },
                    ),
                    (
                        "unsupported_graphic",
                        {
                            "instance_id": -723,
                            "component_type": "Custom.Graphic",
                            "path": "Canvas/CustomGraphic",
                            "path_truncated": False,
                        },
                        {
                            "basis": "registered_graphic_candidate",
                            "retry_unchanged": False,
                            "resource_uri": "mcpforunity://scene/gameobject/-723/components?page_size=20&include_properties=false&evaluate_getters=false",
                        },
                    ),
                ):
                    # A native refusal contains no actual hit order or delivery evidence.
                    reply.update(_unknown_reply(reason, culprit))
                    before = len(calls)
                    result = await client.call_tool("manage_ugui", options)
                    assert result.structured_content == {**reply, "next_observation": expected}
                    assert len(calls) == before + 1
                    assert calls[-1]["allow_custom_filters"] is False
                    reply.clear()
                    reply.update(sample())
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


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_raycast_follow_up_reuses_resolved_instance_across_requests(monkeypatch, mode):
    # Given two Editors and, in legacy mode, a persistent selection of the first.
    module = importlib.import_module("services.tools.manage_ugui")
    search = importlib.import_module("services.tools.find_gameobjects")
    resources = importlib.import_module("services.resources.gameobject")
    registry = importlib.import_module("services.registry")
    first, second = "First@aabb1100", "Second@bbcc2200"
    inventory = [SimpleNamespace(id=value, hash=value.split("@")[1]) for value in (first, second)]
    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(PluginHub, "is_configured", lambda: False)
    middleware = UnityInstanceMiddleware()
    monkeypatch.setattr(middleware, "_discover_instances", AsyncMock(return_value=inventory))
    monkeypatch.setattr(
        middleware, "_resolve_enabled_tool_names_for_context", AsyncMock(return_value=None)
    )
    server = FastMCP("raycast-follow-up-routing")
    server.add_middleware(middleware)
    server.tool(module.manage_ugui)
    server.tool(search.find_gameobjects)
    resource = next(
        item
        for item in registry.get_registered_resources()
        if item["name"] == "gameobject_components"
    )
    server.resource(resource["uri"])(_serialize_pydantic(resources.get_gameobject_components))

    @server.tool
    async def pin_first(ctx: Context) -> None:
        await middleware.set_active_instance(ctx, first)

    async def read(_send, instance, command, _params):
        if command == "manage_ugui":
            assert instance == second
            return copy.deepcopy(reply)
        return {"success": True, "data": {"instance": instance}}

    for service in (module, search, resources):
        monkeypatch.setattr(service, "send_with_unity_instance", read)
    for service in (module, search):
        monkeypatch.setattr(service, "preflight", AsyncMock(return_value=None))

    async with Client(server, mode=mode) as client:
        if mode == "legacy":
            await client.call_tool("pin_first")
        options = {
            "action": "diagnose_raycast",
            "target": 7,
            "screen_position": [100.0, 80.0],
            "unity_instance": "bbcc22",
        }
        # When a diagnostic is explicitly sent to the second Editor via a hash prefix.
        reply = _unknown_reply("missing_event_system")
        diagnosis = await client.call_tool("manage_ugui", options)
        observation = diagnosis.structured_content["next_observation"]
        followed = await client.call_tool(observation["tool"], observation["arguments"])
        # Then both subsequent request forms use the full resolved selector.
        assert observation["arguments"]["unity_instance"] == second
        assert followed.structured_content["data"]["instance"] == second
        reply = _unknown_reply(
            "unsupported_graphic",
            {
                "instance_id": -723,
                "component_type": "Custom.Graphic",
                "path": "Canvas/CustomGraphic",
                "path_truncated": False,
            },
        )
        diagnosis = await client.call_tool("manage_ugui", options)
        observation = diagnosis.structured_content["next_observation"]
        assert observation["meta"]["unity_instance"] == second
        followed = await client.read_resource(observation["resource_uri"], meta=observation["meta"])
        assert json.loads(followed[0].text)["data"]["instance"] == second
