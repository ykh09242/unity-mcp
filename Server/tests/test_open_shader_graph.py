"""Live window counts are conditional evidence and inherit the selected shader parent's read grant."""

import inspect

import pytest
from fastmcp import Client

from services.operation_permissions import Permission, required_permissions
from services.registry.observation_predicates import evaluate_observation
from services.registry.open_shader_graph_observation import OPEN_GRAPH_OBSERVATION
from services.registry.operation_invocation import wrap_tool
from tests.test_editor_observation_resources import (
    GRAPH_PATH,
    OBSERVATION,
    TARGET,
    observation_server,
)
from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import


@pytest.mark.parametrize(
    "status,count,expected",
    [
        ("open_model", 0, True),
        ("open_model", 45, True),
        ("open_model", -1, False),
        ("open_model", False, False),
        ("open_model", 2.5, False),
        ("open_model", "0", False),
        ("no_matching_window", 0, False),
        ("open_model_unavailable", 0, False),
        ("window_lookup_unavailable", 0, False),
    ],
)
def test_only_live_integer_counts_are_evidence(status, count, expected):
    reply = {"success": True, "data": {"status": status, "nodeCount": count, "edgeCount": count}}
    values = ["nodeCount", "edgeCount"]
    assert evaluate_observation(OPEN_GRAPH_OBSERVATION, reply, values) == dict.fromkeys(
        values, expected
    )


@pytest.mark.asyncio
async def test_fixed_read_uses_selected_shader_parent(command_hub, monkeypatch):
    import services.tools.inspect_open_shader_graph as shader_graph_tool
    from services.tools.manage_shader import manage_shader

    name = "inspect_open_shader_graph"
    parent = "manage_shader"
    assert required_permissions(name, {}) == {Permission.READ}
    assert required_permissions(parent, {"action": "inspect_open_graph"}) == {Permission.READ}

    # FastMCP registration can move the injected Context after the required action.
    signature = inspect.signature(manage_shader)
    parameters = dict(signature.parameters)
    action = parameters.pop("action")
    with monkeypatch.context() as registration:
        registration.setattr(
            manage_shader,
            "__signature__",
            signature.replace(parameters=[action, *parameters.values()]),
            raising=False,
        )
        admitted_parent = wrap_tool(manage_shader, parent, parent)
    monkeypatch.setattr(shader_graph_tool, "_read_shader", admitted_parent)

    async with Client(observation_server(monkeypatch), mode="legacy", cache=False) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert tools[name].annotations.read_only_hint
        assert tools[name].meta[OBSERVATION] == OPEN_GRAPH_OBSERVATION
        result = await client.call_tool(
            "search_unity_tools",
            {
                "observation_source": "editor_live",
                "required_values": ["nodeCount", "edgeCount"],
                "route_surfaces": "tools",
            },
        )
        [group] = result.structured_content["route_groups"]
        assert group["representative"]["tool"] == name
        assert group["representative"]["required_parameters"] == ["path"]
        [alternative] = group["alternatives"]
        assert (alternative["tool"], alternative["operation"]) == (parent, "inspect_open_graph")
        response = await client.call_tool(name, {**TARGET, "path": GRAPH_PATH})
        assert response.structured_content["success"]
        [sent] = command_hub.sent
        assert sent["name"] == parent
        assert sent["params"] == {"action": "inspect_open_graph", "path": GRAPH_PATH}
        await client.call_tool("manage_tools", {"action": "deactivate", "tool": parent})
        assert name not in {tool.name for tool in await client.list_tools()}
        assert not (
            await client.call_tool(name, {**TARGET, "path": GRAPH_PATH})
        ).structured_content["success"]
        assert command_hub.sent == [sent]
