"""Feature registration must tolerate prior SDK Context injection on source tools."""

from tests.test_tool_group_status import run_sdk_regression


def test_features_register_once_after_sources_have_been_registered_with_sdk():
    run_sdk_regression("""
        import asyncio, sys
        from importlib import import_module
        sys.path.insert(0, "src")
        from fastmcp import Client, FastMCP
        from core.config import config
        from services.registry import get_registered_tools
        from services.registry.capability_catalog import CAPABILITIES, LEGACY_TOOLS
        from services.tools import register_all_tools

        async def scenario():
            original = FastMCP("original-first")
            for name in sorted(LEGACY_TOOLS):
                module = import_module("services.tools." + name)
                original.tool(getattr(module, name))
            config.transport_mode = "stdio"
            config.http_remote_hosted = False
            derived = FastMCP("features-after-injection")
            register_all_tools(derived, project_scoped_tools=False)
            declared = [entry["name"] for entry in get_registered_tools()]
            assert len(declared) == len(set(declared)), declared
            assert {item.name for item in CAPABILITIES} <= set(declared)
            async with Client(derived, mode="legacy") as client:
                tools = {tool.name: tool for tool in await client.list_tools()}
                assert tools["gameobject_create"].inputSchema["required"] == ["action"]
                assert "ctx" not in tools["gameobject_create"].inputSchema["properties"]
        asyncio.run(scenario())
    """)
