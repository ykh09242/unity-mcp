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
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_group_status_matches_stdio_and_unity_synced_http_visibility():
    run_sdk_regression('''
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
    ''')


def test_group_status_honors_remote_tenants_project_selection_and_empty_catalogs():
    run_sdk_regression('''
        import asyncio, sys
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        from fastmcp import Client, Context, FastMCP
        from core.config import config
        from models.models import ToolDefinitionModel
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

        async def check_groups(ctx: Context, action: str, group: str | None = None) -> dict:
            keys = ("unity_instance", "unity_session_id", "user_id")
            before = [await ctx.get_state(key) for key in keys]
            response = await manage_tools(ctx, action, group)
            assert [await ctx.get_state(key) for key in keys] == before, "Group listing mutated request routing"
            return response

        async def scenario():
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
                middleware = UnityInstanceMiddleware()
                middleware._resolve_user_id = AsyncMock(return_value=user)
                mcp.add_middleware(middleware)
                for mode in ("2026-07-28", "legacy"):
                    async with Client(mcp, mode=mode) as client:
                        response = (await client.call_tool("manage_tools", {"action": "list_groups"})).structured_content
                        enabled = {group["name"] for group in response["groups"] if group["enabled"]}
                        assert enabled == expected, (mode, user, enabled)
                        assert sum(group["tool_count"] for group in response["groups"]) == 3
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
    ''')
