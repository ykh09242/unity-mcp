"""Terrain public admission and registered SDK contracts."""

import asyncio
import importlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

REVISION = "terrain1:" + "a" * 64
GUID = "b" * 32


def test_terrain_tool_is_present():
    assert importlib.util.find_spec("services.tools.manage_terrain") is not None, (
        "SPEC18 requires the dedicated Terrain tool."
    )


@pytest.fixture
def wire(monkeypatch):
    if importlib.util.find_spec("services.tools.manage_terrain") is None:
        pytest.skip("Dedicated tool has not been implemented yet.")
    module = importlib.import_module("services.tools.manage_terrain")
    send = AsyncMock(return_value={"success": True, "data": {"revision": REVISION}})
    context = AsyncMock(return_value="owned")
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    monkeypatch.setattr(module, "get_unity_instance_from_context", context)
    return module, send, context


def invoke(wire, **kwargs):
    return asyncio.run(wire[0].manage_terrain(SimpleNamespace(), target="123", **kwargs))


@pytest.mark.parametrize(
    "channel,values",
    [
        ("heights", [[0.25, 0.5]]),
        ("holes", [[True, False]]),
        ("details", [[0, 2]]),
        ("alphamaps", [[[0.25, 0.75], [1.0, 0.0]]]),
    ],
)
def test_region_edit_exact_wire(wire, channel, values):
    args = dict(
        action="set_region",
        channel=channel,
        region={"x": 2, "y": 3, "width": 2, "height": 1},
        values=values,
        expected_revision=REVISION,
        expected_data_guid=GUID,
    )
    if channel == "details":
        args["detail_layer"] = 0
    assert invoke(wire, **args)["success"]
    assert wire[1].await_args.args[3] == {
        "target": "123",
        **args,
        "allow_shared_data": False,
        "allow_boundary": False,
    }


@pytest.mark.parametrize(
    "change",
    [
        {"region": {"x": True, "y": 1, "width": 1, "height": 1}},
        {"region": {"x": -1, "y": 1, "width": 1, "height": 1}},
        {"region": {"x": 1, "y": 1, "width": 33, "height": 1}},
        {"region": {"x": 1, "y": 1, "width": 1}},
        {"values": [[True]]},
        {"values": [[float("nan")]]},
        {"values": [[1.1]]},
        {"values": []},
        {"values": [[0.2, 0.3]]},
        {"expected_revision": ""},
        {"expected_data_guid": "x"},
        {"channel": "unknown"},
    ],
)
def test_region_refusal_precedes_transport(wire, change):
    args = dict(
        action="set_region",
        channel="heights",
        region={"x": 1, "y": 1, "width": 1, "height": 1},
        values=[[0.25]],
        expected_revision=REVISION,
        expected_data_guid=GUID,
    )
    args.update(change)
    assert invoke(wire, **args)["success"] is False
    wire[1].assert_not_awaited()
    wire[2].assert_not_awaited()


@pytest.mark.parametrize("indices", [[], [1, 1], [True], [-1], list(range(65))])
def test_tree_index_budget_refused(wire, indices):
    assert invoke(wire, action="get_trees", indices=indices)["success"] is False
    wire[1].assert_not_awaited()


def test_tree_and_layer_guards_forwarded(wire):
    assert invoke(
        wire,
        action="set_trees",
        indices=[4],
        values=[{"width_scale": 2}],
        expected_revision=REVISION,
        expected_data_guid=GUID,
    )["success"]
    assert wire[1].await_args.args[3]["indices"] == [4]
    assert invoke(
        wire,
        action="set_layer",
        index=1,
        layer_path="Assets/Owned.terrainlayer",
        layer_guid=GUID,
        expected_revision=REVISION,
        expected_data_guid=GUID,
    )["success"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "action": "get_region",
            "channel": "details",
            "region": {"x": 1, "y": 1, "width": 1, "height": 1},
        },
        {
            "action": "set_trees",
            "indices": [0],
            "values": [{"prototype_index": 1}],
            "expected_revision": REVISION,
            "expected_data_guid": GUID,
        },
        {
            "action": "set_trees",
            "indices": [0],
            "values": [{"color": [256, 0, 0, 0]}],
            "expected_revision": REVISION,
            "expected_data_guid": GUID,
        },
        {
            "action": "set_region",
            "channel": "alphamaps",
            "region": {"x": 1, "y": 1, "width": 1, "height": 1},
            "values": [[[0.3, 0.3]]],
            "expected_revision": REVISION,
            "expected_data_guid": GUID,
        },
        {"action": "get_info", "allow_shared_data": "true"},
        {"action": "get_layer", "index": True},
        {
            "action": "get_region",
            "channel": "heights",
            "region": {"x": 1, "y": 1, "width": 1, "height": 1},
            "values": [[0.1]],
        },
    ],
)
def test_closed_channel_tree_layer_and_acknowledgement_contracts(wire, kwargs):
    assert invoke(wire, **kwargs)["success"] is False
    wire[1].assert_not_awaited()
    wire[2].assert_not_awaited()


def test_registered_terrain_sdk_strict_schema_and_wire():
    code = textwrap.dedent("""
        import asyncio, importlib
        from fastmcp import FastMCP, Client
        from fastmcp.exceptions import ToolError
        from core.logging_decorator import log_execution
        from core.telemetry_decorator import telemetry_tool
        module = importlib.import_module("services.tools.manage_terrain")
        sent = []
        contexts = []
        async def context(ctx):
            contexts.append(True)
            return "owned"
        async def send(fn, instance, command, args):
            assert command == "manage_terrain" and instance == "owned"
            sent.append(args)
            return {"success": True, "data": {"revision": "terrain1:" + "a" * 64}}
        module.get_unity_instance_from_context = context
        module.send_with_unity_instance = send
        server = FastMCP("terrain-contract")
        wrapped = log_execution("manage_terrain", "Tool")(module.manage_terrain)
        server.tool(name="manage_terrain")(telemetry_tool("manage_terrain")(wrapped))
        async def main():
            for mode in ("2026-07-28", "legacy"):
                async with Client(server, mode=mode) as client:
                    args = {"action": "set_region", "target": "123", "search_method": "by_id",
                            "channel": "heights", "region": {"x": 1, "y": 2, "width": 1, "height": 1},
                            "values": [[0.4]], "expected_data_guid": "b" * 32,
                            "expected_revision": "terrain1:" + "a" * 64}
                    result = await client.call_tool("manage_terrain", args)
                    assert result.structured_content["success"]
                    expected = {**args, "searchMethod": "by_id", "allow_shared_data": False, "allow_boundary": False}
                    del expected["search_method"]
                    assert sent[-1] == expected
                    before = (len(sent), len(contexts))
                    for extra in ({"allow_shared_data": "true"}, {"allow_boundary": 1}):
                        try:
                            await client.call_tool("manage_terrain", {**args, **extra})
                        except ToolError:
                            pass
                        else:
                            raise AssertionError("Strict acknowledgement scalar was coerced")
                    bad = {**args, "region": {"x": True, "y": 2, "width": 1, "height": 1}}
                    result = await client.call_tool("manage_terrain", bad)
                    assert result.structured_content["success"] is False
                    assert before == (len(sent), len(contexts))
            print("registered terrain contracts passed")
        asyncio.run(main())
    """)
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "UNITY_MCP_DISABLE_TELEMETRY": "true",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "registered terrain contracts passed" in result.stdout
