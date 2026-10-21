"""Real SDK effect hints describe every action, not just a tool's name."""

import pytest
from tests.test_tool_group_status import run_sdk_regression


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
def test_focused_effect_hints_through_actual_sdk(mode):
    run_sdk_regression(
        """
        import asyncio
        from fastmcp import Client, FastMCP
        from core.config import config
        from services.registry import TOOL_GROUPS
        from services.tools import register_all_tools

        async def scenario():
            config.transport_mode = "stdio"
            config.http_remote_hosted = False
            server = FastMCP("effect-contract")
            register_all_tools(server, project_scoped_tools=False)
            for group in TOOL_GROUPS:
                server.enable(tags={f"group:{group}"}, components={"tool"})
            async with Client(server, mode=MODE) as client:
                tools = {tool.name: tool for tool in await client.list_tools()}
                for name in ("query_physics", "asset_bundles", "asset_reference_diagnostics"):
                    hints = tools[name].annotations
                    assert hints.read_only_hint is True, (name, hints)
                    assert hints.destructive_hint is False, (name, hints)
                    assert hints.open_world_hint is False, (name, hints)
                    assert hints.idempotent_hint is False, (name, hints)
                    assert "Read-only inspection" in tools[name].description
                for name in ("asset_edit", "scene_inspect", "scene_validate", "prefab_contents",
                             "capture_camera", "ui_capture", "manage_rendering_stats",
                             "prefab_inspect", "scene_reference_diagnostics", "ui_inspect"):
                    hints = tools[name].annotations
                    assert hints.read_only_hint is False, (name, hints)
                    assert hints.destructive_hint is True, (name, hints)
                    assert hints.idempotent_hint is False, (name, hints)
                assert tools["diagnose_physics"].annotations.read_only_hint is False
                assert tools["diagnose_physics"].annotations.open_world_hint is False
                provider = tools["generate_image"].annotations
                assert provider.read_only_hint is not True
                assert provider.open_world_hint is not False
                assert provider.destructive_hint is False
        asyncio.run(scenario())
    """.replace("MODE", repr(mode))
    )
