import os
from pathlib import Path
import subprocess
import sys
import textwrap


def run_sdk_regression(source):
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_group_status_matches_stdio_and_unity_synced_http_visibility():
    run_sdk_regression("""
        import asyncio, sys
        sys.path.insert(0, "src")
        from fastmcp import Client, FastMCP
        from core.config import config
        from services.tools.manage_tools import manage_tools
        from services.registry import tool_registry

        tool_registry._tool_registry[:] = [
            {"name": "effect_probe", "group": "vfx"},
            {"name": "scene_probe", "group": "core"},
        ]

        async def scenario():
            for transport in ("stdio", "http"):
                config.transport_mode = transport
                config.http_remote_hosted = False
                mcp = FastMCP("group-status-regression")
                mcp.tool(name="manage_tools")(manage_tools)
                mcp.tool(name="effect_probe", tags={"group:vfx"})(lambda: "effect")
                mcp.tool(name="scene_probe", tags={"group:core"})(lambda: "scene")
                mcp.tool(name="unregistered_custom", tags={"group:testing"})(lambda: "custom")
                if transport == "http":
                    mcp.disable(tags={"group:vfx"}, components={"tool"})
                    mcp.enable(tags={"group:vfx"}, components={"tool"})
                for mode in ("2026-07-28", "legacy"):
                    async with Client(mcp, mode=mode) as first:
                        assert "effect_probe" in {tool.name for tool in await first.list_tools()}
                        groups = (await first.call_tool("manage_tools", {"action": "list_groups"})).structured_content["groups"]
                        group = next(group for group in groups if group["name"] == "vfx")
                        assert group["enabled"] is True, (transport, mode, group)
                        assert next(group for group in groups if group["name"] == "testing")["enabled"] is False
                        if mode == "legacy":
                            async with Client(mcp, mode=mode) as second:
                                await first.call_tool("manage_tools", {"action": "deactivate", "group": "vfx"})
                                groups = (await first.call_tool("manage_tools", {"action": "list_groups"})).structured_content["groups"]
                                assert next(group for group in groups if group["name"] == "vfx")["enabled"] is False
                                other = (await second.call_tool("manage_tools", {"action": "list_groups"})).structured_content["groups"]
                                assert next(group for group in other if group["name"] == "vfx")["enabled"] is True
                                await first.call_tool("manage_tools", {"action": "reset"})
                                reset = (await first.call_tool("manage_tools", {"action": "list_groups"})).structured_content["groups"]
                                assert next(group for group in reset if group["name"] == "vfx")["enabled"] is True
        asyncio.run(scenario())
    """)


def test_group_status_honors_remote_tenants_project_selection_and_empty_catalogs():
    run_sdk_regression("""
        import asyncio, sys
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        from fastmcp import Client, Context, FastMCP
        from mcp.shared.exceptions import MCPError
        from core.config import config
        from models.models import ToolDefinitionModel
        from tests.remote_credentials import remote_credentials
        from services.registry import tool_registry
        from services.tools.manage_tools import manage_tools
        from transport.plugin_hub import PluginHub
        from transport.plugin_registry import PluginRegistry
        from transport.unity_instance_middleware import UnityInstanceMiddleware

        metadata = [
            {"name": "effect_probe", "group": "vfx", "unity_target": "manage_vfx"},
            {"name": "scene_probe", "group": "core", "unity_target": "manage_scene"},
            {"name": "docs_probe", "group": "docs", "unity_target": "manage_docs"},
            {"name": "manage_tools", "group": None, "unity_target": None},
        ]
        tool_registry._tool_registry[:] = metadata
        config.transport_mode = "http"
        config.http_remote_hosted = True

        async def check_groups(ctx: Context, action: str, group: str | None = None, tool: str | None = None) -> dict:
            keys = ("unity_instance", "unity_session_id", "user_id")
            before = [await ctx.get_state(key) for key in keys]
            response = await manage_tools(ctx, action, group, tool)
            assert [await ctx.get_state(key) for key in keys] == before, "Group listing mutated request routing"
            return response

        async def scenario():
            async with remote_credentials({"requester:alice": "alice", "requester:bob": "bob", "requester:empty": "empty", "requester:missing": "missing"}) as credentials:
                class AuthenticatedRouting(UnityInstanceMiddleware):
                    async def on_message(self, context, call_next):
                        async with credentials.requester(self.requester_key):
                            return await super().on_message(context, call_next)

                registry = PluginRegistry()
                PluginHub._registry = registry
                PluginHub._lock = asyncio.Lock()
                for session, project, user, enabled in (
                    ("first", "First", "alice", "manage_vfx"),
                    ("second", "Second", "alice", "manage_scene"),
                    ("bob", "Bob", "bob", "manage_docs"),
                    ("empty", "Empty", "empty", None),
                ):
                    await registry.register(session, project, session, "6000", user_id=user)
                    if enabled:
                        await registry.register_tools_for_session(session, [ToolDefinitionModel(name=enabled)])
                for user, expected in (("alice", {"core", "vfx"}), ("bob", {"docs"}), ("empty", set()), ("missing", set())):
                    mcp = FastMCP(user)
                    for name, group in (("effect_probe", "vfx"), ("scene_probe", "core"), ("docs_probe", "docs")):
                        mcp.tool(name=name, tags={f"group:{group}"})(lambda: "value")
                    mcp.tool(name="unregistered_custom", tags={"group:testing"})(lambda: "custom")
                    mcp.tool(name="manage_tools")(check_groups)
                    middleware = AuthenticatedRouting()
                    middleware.requester_key = f"requester:{user}"
                    middleware._resolve_user_id = AsyncMock(return_value=user)
                    mcp.add_middleware(middleware)
                    for mode in ("2026-07-28", "legacy"):
                        async with Client(mcp, mode=mode) as client:
                            response = (await client.call_tool("manage_tools", {"action": "list_groups"})).structured_content
                            enabled = {group["name"] for group in response["groups"] if group["enabled"]}
                            assert enabled == expected, (mode, user, enabled)
                            assert sum(group["tool_count"] for group in response["groups"]) == 3
                            # Selecting a specific tool must still honor authenticated catalogs.
                            activation = (await client.call_tool("manage_tools", {"action": "activate", "tool": "docs_probe"})).structured_content
                            if mode == "legacy":
                                assert activation["activated_tool"] == "docs_probe"
                            else:
                                assert "sessionless" in activation["error"]
                            after = (await client.call_tool("manage_tools", {"action": "list_groups"})).structured_content
                            assert {group["name"] for group in after["groups"] if group["enabled"]} == expected
                            if user != "bob":
                                try:
                                    denied = await client.call_tool("docs_probe", {}, raise_on_error=False)
                                except MCPError:
                                    pass  # The catalog middleware rejects before tool execution.
                                else:
                                    assert denied.is_error
                            if user == "alice":
                                for target, groups in (("First@first", {"vfx"}), ("Second@second", {"core"})):
                                    selected = (await client.call_tool("manage_tools", {"action": "list_groups", "unity_instance": target})).structured_content
                                    assert {group["name"] for group in selected["groups"] if group["enabled"]} == groups
                                if mode == "legacy":
                                    await client.call_tool("manage_tools", {"action": "deactivate", "group": "vfx"})
                                    hidden = (await client.call_tool("manage_tools", {"action": "list_groups"})).structured_content
                                    assert {group["name"] for group in hidden["groups"] if group["enabled"]} == {"core"}
                                    await client.call_tool("manage_tools", {"action": "reset"})
                                    reset = (await client.call_tool("manage_tools", {"action": "list_groups"})).structured_content
                                    assert {group["name"] for group in reset["groups"] if group["enabled"]} == expected
        asyncio.run(scenario())
    """)


def test_specialized_groups_start_hidden_and_reveal_complete_unchanged_schemas():
    run_sdk_regression("""
        import asyncio, json, sys
        sys.path.insert(0, "src")
        from fastmcp import Client, FastMCP
        from core.config import config
        from services.tools import register_all_tools
        from transport.plugin_hub import PluginHub

        optional = {"capture_camera": "camera", "manage_render_volumes": "graphics", "query_physics": "physics"}

        async def scenario():
            config.transport_mode = "stdio"
            config.http_remote_hosted = False
            complete = FastMCP("complete")
            register_all_tools(complete, project_scoped_tools=False)
            complete.enable(components={"tool"})
            schemas = {t.name: t.parameters for t in await complete.list_tools(run_middleware=False)}
            assert optional.keys() <= schemas.keys(), "Explicit complete catalog"

            config.transport_mode = "http"
            server = FastMCP("lean")
            register_all_tools(server, project_scoped_tools=False)
            for mode in ("legacy", "2026-07-28"):
                async with Client(server, mode=mode) as client:
                    initial = await client.list_tools()
                    names = {t.name for t in initial}
                    assert not optional.keys() & names, names
                    assert {"gameobject_create", "prefab_inspect", "manage_tools"} <= names
                    groups = (await client.call_tool("manage_tools", {"action": "list_groups"})).structured_content["groups"]
                    for name, group in optional.items():
                        item = next(g for g in groups if g["name"] == group)
                        assert name in item["tools"] and not item["default_enabled"] and not item["enabled"]
                        result = (await client.call_tool("manage_tools", {"action": "activate", "group": group})).structured_content
                        if mode == "legacy":
                            assert result["activated"] == group
                            visible = next(t for t in await client.list_tools() if t.name == name)
                            assert visible.inputSchema == schemas[name]
                        else:
                            assert "sessionless" in result["error"]
                    if mode == "legacy":
                        await client.call_tool("manage_tools", {"action": "reset"})
                    assert {t.name for t in await client.list_tools()} == names

            # An explicit Editor choice can publish an optional group to either
            # protocol, without changing the complete input contract.
            PluginHub._mcp = server
            PluginHub._sync_server_tool_visibility([{"name": "capture_camera"}, {"name": "gameobject_create"}])
            async with Client(server, mode="2026-07-28") as client:
                visible = {t.name: t for t in await client.list_tools()}
                assert "capture_camera" in visible
                assert "query_physics" not in visible
                assert visible["capture_camera"].inputSchema == schemas["capture_camera"]
        asyncio.run(scenario())
    """)
