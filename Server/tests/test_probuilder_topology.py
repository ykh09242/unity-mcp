"""Complete ProBuilder topology admission through the public wrapper."""

import asyncio
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

module = importlib.import_module("services.tools.manage_probuilder")
REVISION = "pb1:" + "a" * 64


@pytest.fixture
def wire(monkeypatch):
    send = AsyncMock(return_value={"success": True, "data": {"total": 300}})
    context = AsyncMock(return_value="owned")
    monkeypatch.setattr(module, "send_with_unity_instance", send)
    monkeypatch.setattr(module, "get_unity_instance_from_context", context)
    return send, context


def call(action, properties):
    return asyncio.run(
        module.manage_probuilder(SimpleNamespace(), action, target="123", properties=properties)
    )


@pytest.mark.parametrize("as_json", [False, True])
def test_topology_pages_preserve_exact_wire(wire, as_json):
    properties = {"kind": "edges", "page_size": 37, "cursor": "opaque", "mesh_revision": REVISION}
    supplied = json.dumps(properties) if as_json else properties
    result = call("get_topology", supplied)
    assert result["success"]
    assert wire[0].await_args.args[3] == {
        "action": "get_topology",
        "target": "123",
        "properties": supplied,
    }


@pytest.mark.parametrize(
    "properties",
    [
        None,
        [],
        "[]",
        "{",
        {"kind": "all"},
        {"kind": True},
        {"page_size": 0},
        {"page_size": 101},
        {"page_size": True},
        {"page_size": 1.5},
        {"page_size": "10"},
        {"cursor": ""},
        {"cursor": "x"},
        {"cursor": "x" * 2049, "mesh_revision": REVISION},
        {"mesh_revision": "a" * 64},
        {"mesh_revision": None},
        {"offset": 10},
    ],
)
def test_malformed_topology_refused_before_context_or_transport(wire, properties):
    result = call("get_topology", properties)
    assert result["success"] is False
    wire[0].assert_not_awaited()
    wire[1].assert_not_awaited()


@pytest.mark.parametrize("action", ["move_vertices", "delete_faces", "extrude_edges"])
def test_revision_guard_wire_preserved(wire, action):
    properties = {"mesh_revision": REVISION, "vertexIndices": [200], "offset": [1, 0, 0]}
    assert call(action, properties)["success"]
    assert wire[0].await_args.args[3]["properties"] == properties


@pytest.mark.parametrize("action", ["ping", "create_shape", "get_mesh_info", "combine_meshes"])
def test_guard_on_unsupported_action_refused(wire, action):
    assert call(action, {"mesh_revision": REVISION})["success"] is False
    wire[0].assert_not_awaited()


@pytest.mark.parametrize("revision", [True, 1, "", "pb1:" + "A" * 64, "pb1:" + "a" * 65])
def test_malformed_mutation_guard_refused(wire, revision):
    assert call("move_vertices", {"mesh_revision": revision})["success"] is False
    wire[0].assert_not_awaited()


def test_legacy_unvalidated_properties_remain_unchanged(wire):
    properties = '{"vertexIndices":["0"],"offset":"legacy-invalid"}'
    assert call("move_vertices", properties)["success"]
    assert wire[0].await_args.args[3]["properties"] == properties


def test_topology_through_registered_sdk_both_protocol_modes():
    code = textwrap.dedent("""
        import asyncio, importlib, json
        from fastmcp import FastMCP, Client
        from core.logging_decorator import log_execution
        from core.telemetry_decorator import telemetry_tool
        module = importlib.import_module("services.tools.manage_probuilder")
        sent = []
        contexts = []
        async def context(ctx):
            contexts.append(True)
            return "owned"
        async def send(fn, instance, command, params):
            assert instance == "owned" and command == "manage_probuilder"
            sent.append(params)
            return {"success": True, "data": {"total": 500, "items": [{"index": 499}]}}
        module.get_unity_instance_from_context = context
        module.send_with_unity_instance = send
        server = FastMCP("probuilder-topology-contract")
        wrapped = log_execution("manage_probuilder", "Tool")(module.manage_probuilder)
        server.tool(name="manage_probuilder")(telemetry_tool("manage_probuilder")(wrapped))
        async def main():
            for mode in ("2026-07-28", "legacy"):
                async with Client(server, mode=mode) as client:
                    props = {"kind": "edges", "page_size": 37, "cursor": "opaque", "mesh_revision": "pb1:" + "a" * 64}
                    for wire in (props, json.dumps(props)):
                        result = await client.call_tool("manage_probuilder", {"action": "get_topology", "target": "123", "search_method": "by_id", "properties": wire})
                        assert result.structured_content["success"]
                        assert result.structured_content["data"]["items"] == [{"index": 499}]
                        assert sent[-1] == {"action": "get_topology", "target": "123", "searchMethod": "by_id", "properties": wire}
                    before = (len(sent), len(contexts))
                    for invalid in ({"page_size": True}, {"page_size": "37"}, {"cursor": "x"}, {"mesh_revision": False}):
                        result = await client.call_tool("manage_probuilder", {"action": "get_topology", "target": "123", "properties": invalid})
                        assert result.structured_content["success"] is False
                    assert before == (len(sent), len(contexts))
            print("registered topology contracts passed")
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
    assert "registered topology contracts passed" in result.stdout
