"""ProBuilder wire/error contracts through the real SDK, with controlled transport."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_probuilder_preserves_mesh_inputs_and_unity_failures_through_sdk():
    code = textwrap.dedent('''
        import asyncio, importlib, json
        from fastmcp import FastMCP, Client
        from core.logging_decorator import log_execution
        from core.telemetry_decorator import telemetry_tool
        module = importlib.import_module("services.tools.manage_probuilder")
        sent = []
        failure = {"success": False, "error": "controlled Unity validation failure"}
        async def instance(ctx): return None
        async def send(fn, instance, command, params):
            assert command == "manage_probuilder"
            sent.append(params)
            return failure
        module.get_unity_instance_from_context = instance
        module.send_with_unity_instance = send
        server = FastMCP("probuilder-contract")
        wrapped = log_execution("manage_probuilder", "Tool")(module.manage_probuilder)
        server.tool(name="manage_probuilder")(telemetry_tool("manage_probuilder")(wrapped))
        async def main():
            cases = (
                ("set_pivot", {"position": "garbage"}),
                ("move_vertices", {"vertexIndices": [0], "offset": [1, 2]}),
                ("extrude_edges", {"edges": [{"a": 1}]}),
                ("extrude_edges", {"edges": [{"a": "0", "b": 1.0}], "distance": 0, "asGroup": False}),
                ("move_vertices", {"vertexIndices": ["0", 0], "offset": ["1", "0", "0"]}),
                ("set_smoothing", {"faceIndices": None, "smoothingGroup": 0}),
                ("subdivide", {"faceIndices": []}),
            )
            for mode in ("2026-07-28", "legacy"):
                async with Client(server, mode=mode) as client:
                    for action, properties in cases:
                        for wire in (properties, json.dumps(properties)):
                            result = await client.call_tool("manage_probuilder", {"action": action, "target": "123", "search_method": "by_name", "properties": wire})
                            assert result.structured_content == failure
                            assert sent[-1] == {"action": action, "target": "123", "searchMethod": "by_name", "properties": wire}
                    for properties in (None, {}):
                        result = await client.call_tool("manage_probuilder", {"action": "ping", "properties": properties})
                        assert result.structured_content == failure
                        assert sent[-1] == ({"action": "ping"} if properties is None else {"action": "ping", "properties": {}})
            print("real SDK ProBuilder wire and failure contracts passed")
        asyncio.run(main())
    ''')
    result = subprocess.run(
        [sys.executable, "-B", "-c", code], capture_output=True, text=True, timeout=30,
        env={**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "true",
             "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "real SDK ProBuilder wire and failure contracts passed" in result.stdout


def test_probuilder_cli_preserves_selector_and_zero_edge_controls():
    from cli.commands.probuilder import _normalize_pb_params, _parse_edges_param

    pairs = _parse_edges_param('[{"a":"0","b":1.0}]')
    request = _normalize_pb_params({
        "action": "extrude_edges", "target": "123", "searchMethod": "by_name",
        "distance": 0, "asGroup": False, **pairs,
    })
    assert request == {
        "action": "extrude_edges", "target": "123", "searchMethod": "by_name",
        "properties": {"distance": 0, "asGroup": False, "edges": [{"a": "0", "b": 1.0}]},
    }
