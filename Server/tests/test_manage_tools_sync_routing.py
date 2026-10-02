from unittest.mock import AsyncMock, Mock

import pytest

from core.config import config
import services.tools as tools
from services.tools.manage_tools import manage_tools


@pytest.mark.asyncio
async def test_manage_tools_sync_uses_the_request_selected_editor(monkeypatch):
    ctx = Mock()
    ctx.get_state = AsyncMock(return_value="EditorB@hash-b")
    ctx.info = AsyncMock()
    sync = AsyncMock(return_value={"synced": True})
    monkeypatch.setattr(tools, "sync_tool_visibility_from_unity", sync)

    result = await manage_tools(ctx, "sync")

    assert result["synced"] is True
    sync.assert_awaited_once_with(instance_id="EditorB@hash-b", notify=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["stdio", "http"])
async def test_tool_sync_uses_the_configured_unity_transport(monkeypatch, mode):
    monkeypatch.setattr(config, "transport_mode", mode)
    monkeypatch.setattr(config, "http_remote_hosted", False)
    send = AsyncMock(return_value={"success": True, "data": {"tools": [
        {"name": "read_console", "enabled": True},
    ]}})
    legacy = AsyncMock(return_value={"success": False, "error": "TCP is unavailable"})
    monkeypatch.setattr("transport.unity_transport.send_with_unity_instance", send)
    monkeypatch.setattr("transport.legacy.unity_connection.async_send_command_with_retry", legacy)
    sync = Mock()
    monkeypatch.setattr("transport.plugin_hub.PluginHub._sync_server_tool_visibility", sync)

    result = await tools.sync_tool_visibility_from_unity("EditorB@hash-b", notify=False)

    assert result["synced"] is True
    send.assert_awaited_once_with(legacy, "EditorB@hash-b", "get_tool_states", {})
    legacy.assert_not_awaited()
    sync.assert_called_once_with([{"name": "read_console", "enabled": True}])
