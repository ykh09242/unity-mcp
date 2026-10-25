"""Native implementation names do not create duplicate public tool contracts."""

import pytest

from tests.test_tool_group_status import run_sdk_regression


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_native_handle_catalog_preserves_only_the_validated_public_wrapper(mode):
    run_sdk_regression(
        """
        import asyncio
        import json
        import sys
        sys.path.insert(0, "src")
        from unittest.mock import AsyncMock
        from fastmcp import Client, FastMCP
        from core.config import config
        from models.models import ToolDefinitionModel
        from services.custom_tool_service import CustomToolService
        from services.registry import get_registered_tools
        import services.tools as tools
        from services.tools import manage_object_handles as module

        async def scenario():
            # Given: the native catalog names the implementation behind a public wrapper.
            config.transport_mode = "stdio"
            config.http_remote_hosted = False
            metadata = [row for row in get_registered_tools() if row["name"] == "manage_object_handles"]
            tools.discover_modules = lambda *args: []
            tools.get_registered_tools = lambda: metadata
            module.preflight = AsyncMock(return_value=None)
            module.get_unity_instance_from_context = AsyncMock(return_value="Fixture@hash")
            send = module.send_with_unity_instance = AsyncMock(return_value={"success": True})
            server = FastMCP("native-alias")
            tools.register_all_tools(server, project_scoped_tools=False)
            service = CustomToolService(server, project_scoped_tools=False)
            # When: a legacy native descriptor with no parameter schema is published.
            service.register_global_tools([ToolDefinitionModel(name="manage_object_handles_v2")])
            # Then: callers keep the validated schema and native dispatch route.
            async with Client(server, mode=MODE) as client:
                listed = {tool.name: tool for tool in await client.list_tools()}
                assert set(listed) == {"manage_object_handles"}
                assert "action" in listed["manage_object_handles"].input_schema["required"]
                result = await client.call_tool("manage_object_handles", {"action": "capture", "instance_id": -12})
                assert json.loads(result.content[0].text)["success"] is True
                assert send.await_args.args[2:] == ("manage_object_handles_v2", {"action": "capture", "instance_id": -12})
                assert "manage_object_handles_v2" in service._sources[("global", None, "shared")].tools
        asyncio.run(scenario())
        """.replace("MODE", repr(mode))
    )
