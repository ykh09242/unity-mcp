"""Exercise plugin tool publication through the installed MCP SDK."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def run_registration_scenario(source, tmp_path):
    # Legacy integration modules replace SDK modules during collection.
    env = {
        **os.environ,
        "UNITY_MCP_DISABLE_TELEMETRY": "1",
        "APPDATA": str(tmp_path / "appdata"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
    }
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_notification_observes_published_custom_tools(tmp_path):
    run_registration_scenario(
        """
        import asyncio
        import sys
        import weakref
        from unittest.mock import AsyncMock

        sys.path.insert(0, "src")
        from fastmcp import Client, FastMCP
        from core.config import config
        from services.custom_tool_service import CustomToolService
        import transport.plugin_hub as module
        from transport.plugin_hub import PluginHub
        from transport.plugin_registry import PluginRegistry

        async def scenario():
            config.http_remote_hosted = False
            mcp = FastMCP("plugin-publication-regression")
            service = CustomToolService(mcp)
            registry = PluginRegistry()
            PluginHub.configure(registry, mcp=mcp)
            socket = AsyncMock()
            await registry.register("session", "Project", "hash", "6000")
            PluginHub._connections["session"] = socket
            hub = PluginHub({"type": "websocket"}, AsyncMock(), AsyncMock())

            async with Client(mcp) as client:
                observed = []

                class CatalogObserver:
                    async def send_tool_list_changed(self):
                        # A client may re-fetch before the notification send completes.
                        observed.append({tool.name for tool in await client.list_tools()})

                observer = CatalogObserver()
                module._active_mcp_sessions = weakref.WeakSet([observer])
                await hub.on_receive(socket, {
                    "type": "register_tools",
                    "tools": [{"name": "published_custom", "description": "Published tool"}],
                })
                assert "published_custom" in (await registry.get_session("session")).tools
                assert "published_custom" in service._global_tools
                assert observed == [{"published_custom"}], observed

            await PluginHub.shutdown()

        asyncio.run(scenario())
    """,
        tmp_path,
    )


def test_unregistered_and_hosted_plugins_do_not_publish_global_tools(tmp_path):
    run_registration_scenario(
        """
        import asyncio
        import sys
        from unittest.mock import AsyncMock, patch

        sys.path.insert(0, "src")
        from fastmcp import FastMCP
        from core.config import config
        from services.custom_tool_service import CustomToolService
        from transport.plugin_hub import PluginHub
        from transport.plugin_registry import PluginRegistry

        async def scenario():
            config.http_remote_hosted = False
            mcp = FastMCP("plugin-publication-boundaries")
            service = CustomToolService(mcp)
            registry = PluginRegistry()
            PluginHub.configure(registry, mcp=mcp)
            socket = AsyncMock()
            hub = PluginHub({"type": "websocket"}, AsyncMock(), AsyncMock())
            payload = {"type": "register_tools", "tools": [{"name": "private_custom"}]}

            with patch.object(PluginHub, "_notify_mcp_tool_list_changed", new_callable=AsyncMock) as notify:
                await hub.on_receive(socket, payload)
                assert await mcp.list_tools() == []
                assert service._global_tools == {}
                notify.assert_not_awaited()

                config.http_remote_hosted = True
                await registry.register("session", "Project", "hash", "6000", user_id="owner")
                PluginHub._connections["session"] = socket
                await hub.on_receive(socket, payload)
                assert "private_custom" in (await registry.get_session("session")).tools
                assert await mcp.list_tools() == []
                assert service._global_tools == {}
                notify.assert_not_awaited()

            await PluginHub.shutdown()

        asyncio.run(scenario())
    """,
        tmp_path,
    )
