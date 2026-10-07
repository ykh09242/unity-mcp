"""Exercise read-only inspection actions through the registered SDK and transport boundary."""

import os
import subprocess
import sys
import textwrap


def test_bundle_and_graph_inspection_sdk_contracts(tmp_path):
    program = textwrap.dedent(r"""
        import copy
        import anyio
        from fastmcp import FastMCP, Client
        from fastmcp.server.middleware import Middleware
        from services.tools import register_all_tools
        from core.config import config
        from transport.plugin_hub import PluginHub

        config.transport_mode = "http"
        config.http_remote_hosted = False
        requests = []
        raw = {"success": True, "message": "Inspected", "data": {
            "entries": [], "totalEntries": 0, "hasMore": False,
        }}

        async def controlled_send(instance, command, params, **kwargs):
            requests.append((instance, command, copy.deepcopy(params)))
            return copy.deepcopy(raw)

        PluginHub.send_command_for_instance = staticmethod(controlled_send)

        class FixtureState(Middleware):
            async def on_call_tool(self, context, call_next):
                await context.fastmcp_context.set_state("unity_instance", "Project@inspection")
                return await call_next(context)

        server = FastMCP("inspection-contracts")
        server.add_middleware(FixtureState())
        register_all_tools(server)
        server.enable(tags={"group:vfx"})

        async def main():
            global raw
            for mode in ("2026-07-28", "legacy"):
                async with Client(server, mode=mode) as client:
                    tools = {tool.name: tool for tool in await client.list_tools()}
                    asset_actions = tools["manage_asset"].inputSchema["properties"]["action"]["enum"]
                    shader_actions = tools["manage_shader"].inputSchema["properties"]["action"]["enum"]
                    assert set(("list_asset_bundles", "get_bundle_assets", "get_bundle_dependencies")) <= set(asset_actions)
                    assert "inspect_graph" in shader_actions

                    for action in ("list_asset_bundles", "get_bundle_assets", "get_bundle_dependencies"):
                        payload = {"action": action, "page_size": "2", "page_number": "3"}
                        if action != "list_asset_bundles":
                            payload["bundle_name"] = "fixture.bundle"
                        if action == "get_bundle_dependencies":
                            payload["recursive"] = True
                        before = len(requests)
                        result = await client.call_tool("manage_asset", payload)
                        assert result.structured_content == raw
                        # Read-only inspection must not invoke preflight refresh or other tools.
                        assert len(requests) == before + 1
                        instance, command, wire = requests[-1]
                        assert (instance, command) == ("Project@inspection", "manage_asset")
                        assert "path" not in wire
                        assert (wire["action"], wire["pageSize"], wire["pageNumber"]) == (action, 2, 3)
                        if action != "list_asset_bundles":
                            assert wire["bundleName"] == "fixture.bundle"
                        if action == "get_bundle_dependencies":
                            assert wire["recursive"] is True

                    for action in (
                        "import", "create", "modify", "delete", "duplicate", "move", "rename",
                        "get_info", "create_folder", "get_components",
                    ):
                        before = len(requests)
                        result = await client.call_tool("manage_asset", {"action": action})
                        assert result.structured_content["success"] is False
                        assert "requires parameter 'path'" in result.structured_content["message"]
                        assert len(requests) == before

                    raw = {"success": True, "message": "Inspected", "data": {
                        "nodes": [], "edges": [], "properties": [], "keywords": [],
                        "nodeCount": 0, "edgeCount": 0, "hasMore": False,
                    }}
                    result = await client.call_tool("manage_shader", {
                        "action": "inspect_graph", "path": "Assets/Graph with spaces.shadergraph",
                        "page_size": "10", "page_number": "2",
                    })
                    assert result.structured_content == raw
                    assert requests[-1] == ("Project@inspection", "manage_shader", {
                        "action": "inspect_graph", "path": "Assets/Graph with spaces.shadergraph",
                        "pageSize": 10, "pageNumber": 2,
                    })

                    for tool, payload in (
                        ("manage_shader", {"action": "inspect_graph", "path": "Assets/Graph.shadergraph", "page_size": 101}),
                        ("manage_shader", {"action": "inspect_graph", "path": "Assets/Graph.shadergraph", "page_number": 0}),
                        ("manage_asset", {"action": "list_asset_bundles", "page_size": 0}),
                        ("manage_asset", {"action": "list_asset_bundles", "page_size": 101}),
                        ("manage_asset", {"action": "get_bundle_assets"}),
                        ("manage_asset", {"action": "get_bundle_dependencies", "bundle_name": " "}),
                    ):
                        before = len(requests)
                        result = await client.call_tool(tool, payload)
                        assert result.structured_content["success"] is False
                        assert len(requests) == before

                    raw = {"success": False, "code": "unsupported_format", "error": "Unsupported format", "data": {"count": 0}}
                    for tool, payload in (
                        ("manage_shader", {"action": "inspect_graph", "path": "Assets/Graph.shadergraph"}),
                        ("manage_asset", {"action": "get_bundle_assets", "bundle_name": "unknown"}),
                    ):
                        result = await client.call_tool(tool, payload)
                        assert result.structured_content == raw
            print("inspection SDK contracts passed in both modes")

        anyio.run(main)
    """)
    env = {
        **os.environ,
        "APPDATA": str(tmp_path),
        "XDG_DATA_HOME": str(tmp_path),
        "UNITY_MCP_DISABLE_TELEMETRY": "true",
    }
    env.pop("PYTEST_CURRENT_TEST", None)
    result = subprocess.run(
        [sys.executable, "-B", "-c", program],
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "inspection SDK contracts passed in both modes" in result.stdout
