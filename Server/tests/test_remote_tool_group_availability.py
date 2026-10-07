import os
from pathlib import Path
import subprocess
import sys
import textwrap


def run_sdk_regression(source):
    # Legacy collection stubs SDK modules, so exercise the installed SDK separately.
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "UNITY_MCP_DISABLE_TELEMETRY": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_remote_optional_groups_follow_each_tenants_catalog_through_real_sdk():
    run_sdk_regression("""
        import asyncio, sys
        from unittest.mock import AsyncMock
        sys.path.insert(0, "src")
        from fastmcp import Client, Context, FastMCP
        from core.config import config
        from models.models import ToolDefinitionModel
        import services.tools as tools
        import transport.unity_instance_middleware as routing
        from transport.plugin_hub import PluginHub
        from transport.plugin_registry import PluginRegistry

        async def effect(ctx: Context) -> str:
            return await ctx.get_state("user_id")

        async def scene(ctx: Context) -> str:
            return await ctx.get_state("user_id")

        metadata = [
            {"name": "manage_vfx", "func": effect, "description": "effect", "kwargs": {"tags": {"group:vfx"}}, "unity_target": "manage_vfx"},
            {"name": "manage_scene", "func": scene, "description": "scene", "kwargs": {"tags": {"group:core"}}, "unity_target": "manage_scene"},
        ]
        tools.discover_modules = lambda *args: []
        tools.get_registered_tools = lambda: metadata
        routing.get_registered_tools = lambda: metadata
        config.transport_mode = "http"
        config.http_remote_hosted = True

        async def scenario():
            mcp = FastMCP("remote-optional-groups")
            tools.register_all_tools(mcp)
            registry = PluginRegistry()
            PluginHub._registry = registry
            PluginHub._mcp = mcp
            PluginHub._lock = asyncio.Lock()
            for user, enabled in (("alice", "manage_vfx"), ("bob", "manage_scene")):
                await registry.register(user, "Project", "shared", "6000", user_id=user)
                await registry.register_tools_for_session(user, [ToolDefinitionModel(name=enabled)])
            middleware = routing.UnityInstanceMiddleware()
            mcp.add_middleware(middleware)
            for mode in ("2026-07-28", "legacy"):
                for user, expected in (("alice", "manage_vfx"), ("bob", "manage_scene")):
                    middleware._resolve_user_id = AsyncMock(return_value=user)
                    async with Client(mcp, mode=mode) as client:
                        names = {tool.name for tool in await client.list_tools()}
                        assert names == {expected}, (mode, user, names)
                        result = await client.call_tool(expected, {"unity_instance": "Project@shared"})
                        assert not result.is_error
                        assert result.content[0].text == user
        asyncio.run(scenario())
    """)


def test_local_http_optional_groups_keep_legacy_session_activation_isolated():
    run_sdk_regression("""
        import asyncio, sys
        sys.path.insert(0, "src")
        from fastmcp import Client, FastMCP
        from core.config import config
        import services.tools as tools
        from services.tools.manage_tools import manage_tools

        def effect() -> str:
            return "effect"

        metadata = [
            {"name": "effect_probe", "func": effect, "description": "effect", "kwargs": {"tags": {"group:vfx"}}},
            {"name": "manage_tools", "func": manage_tools, "description": "groups", "kwargs": {}},
        ]
        tools.discover_modules = lambda *args: []
        tools.get_registered_tools = lambda: metadata
        config.transport_mode = "http"
        config.http_remote_hosted = False

        async def scenario():
            mcp = FastMCP("local-optional-groups")
            tools.register_all_tools(mcp)
            async with Client(mcp, mode="legacy") as first, Client(mcp, mode="legacy") as second:
                assert "effect_probe" not in {tool.name for tool in await first.list_tools()}
                await first.call_tool("manage_tools", {"action": "activate", "group": "vfx"})
                assert "effect_probe" in {tool.name for tool in await first.list_tools()}
                assert "effect_probe" not in {tool.name for tool in await second.list_tools()}
                await first.call_tool("manage_tools", {"action": "deactivate", "group": "vfx"})
                assert "effect_probe" not in {tool.name for tool in await first.list_tools()}
                await first.call_tool("manage_tools", {"action": "activate", "group": "vfx"})
                await first.call_tool("manage_tools", {"action": "reset"})
                assert "effect_probe" not in {tool.name for tool in await first.list_tools()}
        asyncio.run(scenario())
    """)
