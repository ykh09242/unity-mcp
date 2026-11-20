"""Verify isolated presentation changes and source-specific evidence projections."""

import json
from types import SimpleNamespace

import pytest
from fastmcp import FastMCP
from fastmcp.tools.base import ToolResult
from mcp_types import Tool

from tools.experiments.blind_tool_factors import CAMERA_SENTENCE, alternate_order, install_factor
from tools.experiments.blind_tool_trace import observation, score_case, unpack


def test_window_evidence_requires_successful_typed_window_inventory():
    route = "mcpforunity://editor/windows"
    for windows, expected in [
        ([], {}),
        ([{"title": "Shader Graph"}], {}),
        (
            [{"typeName": "UnityEditor.InspectorWindow", "title": "private"}],
            {"shader_graph_window_listed": False},
        ),
        (
            [{"typeName": "UnityEditor.ShaderGraph.Drawing.MaterialGraphEditWindow"}],
            {"shader_graph_window_listed": True},
        ),
    ]:
        result = {"structuredContent": {"success": True, "data": windows}}
        assert observation(route, unpack({"result": result}, route)[1]) == expected
        result["structuredContent"]["success"] = False
        assert unpack({"result": result}, route) == ("error", {})


def test_wire_links_projection_only_retains_ranked_public_identifiers():
    uri = "mcpforunity://scene/cameras"
    payload = {"scope": "local_catalog", "routes": [{"kind": "resource", "uri": uri}]}
    reply = {
        "structuredContent": payload,
        "content": [
            {"type": "text", "text": json.dumps(payload)},
            {"type": "resource_link", "uri": uri},
            {"type": "resource_link", "uri": "file:///private/path"},
        ],
    }
    route = "search_unity_tools"
    facts = observation(route, unpack({"result": reply}, route)[1])
    assert facts["linked_top3"] == [uri]
    assert facts["flat_top3"] == [uri]
    assert facts["resource_link_count"] == 2
    del reply["content"]
    assert "resource_link_count" not in observation(route, unpack({"result": reply}, route)[1])


@pytest.mark.parametrize("listed, expected", [(False, 1), (True, None)])
def test_window_list_is_a_limitation_witness_only_when_the_window_is_absent(
    tmp_path, listed, expected
):
    (tmp_path / "case.json").write_text(
        json.dumps(
            {
                "task": "graph-open",
                "role": "main",
                "language": "ko",
                "repetition": 1,
                "graph_asset": "Assets/Sprite.shadergraph",
                "answer_protocol": "v3",
            }
        )
    )
    missing = {"value": None, "source": "unknown", "channel": None}
    (tmp_path / "answer.txt").write_text(
        json.dumps(dict.fromkeys(("nodes", "edges", "node_types", "data_state"), missing))
    )
    rows = [
        {
            "event": "request",
            "sequence": 1,
            "channel": "unity",
            "route": "mcpforunity://editor/state",
        },
        {"event": "response", "sequence": 1, "outcome": "returned", "observation": {}},
        {
            "event": "request",
            "sequence": 2,
            "channel": "unity",
            "route": "mcpforunity://editor/windows",
        },
        {
            "event": "response",
            "sequence": 2,
            "outcome": "returned",
            "observation": {"shader_graph_window_listed": listed},
        },
    ]
    (tmp_path / "mcp-1.jsonl").write_text("\n".join(map(json.dumps, rows)))
    result = score_case(tmp_path)
    assert result["calls_before_window_absence"] == expected
    assert result["first_successful_suitable_call"] is None
    assert result["final_answer"]["status"] == ("limited" if listed else "correct")


@pytest.mark.asyncio
async def test_single_factor_changes_only_its_protocol_surface():
    data = {
        "routes": [{"kind": "resource", "uri": str(i)} for i in range(3)],
        "route_groups": [
            {"representative": {"name": str(i)}, "alternatives": [i]} for i in range(3)
        ],
        "unrelated": [1, 2],
    }

    def tool(name):
        return Tool(
            name=name, description="Camera. " + CAMERA_SENTENCE, inputSchema={"type": "object"}
        )

    tools = [tool("manage_camera"), tool("manage_cameras"), tool("other")]
    result = ToolResult(structured_content=data)

    async def listing(_):
        return tools

    async def searching(_):
        return result

    context = SimpleNamespace(message=SimpleNamespace(name="search_unity_tools", arguments={}))
    server = FastMCP("trial")
    install_factor(server, "camera-description")
    middleware = server.middleware[0]
    changed = await middleware.on_list_tools(context, listing)
    assert [item.description for item in changed] == ["Camera.", "Camera.", tools[2].description]
    assert (await middleware.on_call_tool(context, searching)) is result

    server = FastMCP("trial")
    install_factor(server, "route-order")
    middleware = server.middleware[0]
    assert await middleware.on_list_tools(context, listing) is tools
    changed = await middleware.on_call_tool(context, searching)
    assert changed.structured_content["routes"] == data["routes"][::-1]
    assert changed.structured_content["route_groups"] == data["route_groups"][::-1]
    assert changed.structured_content == json.loads(changed.content[0].text)
    assert alternate_order(changed.structured_content) == data
    context.message.name = "manage_tools"
    context.message.arguments = {"action": "describe"}
    assert await middleware.on_call_tool(context, searching) is result
