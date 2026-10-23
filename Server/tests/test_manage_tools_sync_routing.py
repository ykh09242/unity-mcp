import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from core.config import config
from models.models import ToolDefinitionModel
from services.custom_tool_service import CustomToolService
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry
import services.tools as tools
from services.tools.manage_tools import manage_tools


@pytest.mark.asyncio
async def test_manage_tools_sync_uses_the_request_selected_editor(monkeypatch):
    ctx = Mock()
    ctx.get_state = AsyncMock(return_value="EditorB@hash-b")
    ctx.info = AsyncMock(side_effect=AssertionError("Sync must not require client logging"))
    sync = AsyncMock(return_value={"synced": True})
    monkeypatch.setattr(tools, "sync_tool_visibility_from_unity", sync)

    result = await manage_tools(ctx, "sync")

    assert result["synced"] is True
    sync.assert_awaited_once_with(instance_id="EditorB@hash-b", notify=True)
    ctx.info.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["stdio", "http"])
async def test_tool_sync_uses_the_configured_unity_transport(monkeypatch, mode):
    monkeypatch.setattr(config, "transport_mode", mode)
    monkeypatch.setattr(config, "http_remote_hosted", False)
    registry = PluginRegistry()
    await registry.register("selected", "EditorB", "hash-b", "6000")
    monkeypatch.setattr(PluginHub, "_registry", registry)
    monkeypatch.setattr(PluginHub, "_lock", asyncio.Lock())
    monkeypatch.setattr(PluginHub, "_connections", {"selected": object()})
    monkeypatch.setattr(PluginHub, "_mcp", None)
    monkeypatch.setattr(CustomToolService, "_instance", None)
    monkeypatch.setattr(tools, "get_registered_tools", lambda: [{"unity_target": "read_console"}])
    send = AsyncMock(
        return_value={
            "success": True,
            "data": {
                "tools": [
                    {"name": "read_console", "enabled": True},
                ]
            },
        }
    )
    legacy = AsyncMock(return_value={"success": False, "error": "TCP is unavailable"})
    monkeypatch.setattr("transport.unity_transport.send_with_unity_instance", send)
    monkeypatch.setattr("transport.legacy.unity_connection.async_send_command_with_retry", legacy)
    publish_catalog = PluginHub._publish_session_catalog

    def publish(current, prepared, sessions, **options):
        assert PluginHub._lock.locked() and registry._lock.locked()
        assert current.tools == {}, "Publication must precede the registry commit"
        return publish_catalog(current, prepared, sessions, **options)

    publication = Mock(side_effect=publish)
    monkeypatch.setattr(PluginHub, "_publish_session_catalog", publication)

    result = await tools.sync_tool_visibility_from_unity("EditorB@hash-b", notify=False)

    assert result["synced"] is True
    options = {"retry_on_reload": False}
    if mode == "stdio":
        options["_catalog_capture"] = []
    send.assert_awaited_once_with(legacy, "EditorB@hash-b", "get_tool_states", {}, **options)
    legacy.assert_not_awaited()
    session = await registry.get_session("selected")
    if mode == "http":
        publication.assert_called_once()
        assert session.tools == {"read_console": ToolDefinitionModel(name="read_console")}
        assert session.tools_known and session.tools_revision == 1
    else:
        publication.assert_not_called()
        assert session.tools == {} and not session.tools_known and session.tools_revision == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["lock", "connection"])
async def test_http_tool_sync_refuses_unowned_catalog_without_mutation(monkeypatch, missing):
    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    registry = PluginRegistry()
    await registry.register("selected", "EditorB", "hash-b", "6000")
    monkeypatch.setattr(PluginHub, "_registry", registry)
    monkeypatch.setattr(PluginHub, "_lock", None if missing == "lock" else asyncio.Lock())
    monkeypatch.setattr(
        PluginHub, "_connections", {} if missing == "connection" else {"selected": object()}
    )
    monkeypatch.setattr(CustomToolService, "_instance", None)
    session = await registry.get_session("selected")
    original_tools = session.tools
    send = AsyncMock(
        return_value={
            "success": True,
            "data": {"tools": [{"name": "read_console", "enabled": True}]},
        }
    )
    monkeypatch.setattr("transport.unity_transport.send_with_unity_instance", send)
    publication = Mock(side_effect=AssertionError("An unowned catalog must not publish"))
    monkeypatch.setattr(PluginHub, "_publish_session_catalog", publication)

    result = await tools.sync_tool_visibility_from_unity("EditorB@hash-b", notify=False)

    assert result == {
        "error": "Unity catalog owner unavailable"
        if missing == "lock"
        else "Unity catalog generation changed"
    }
    publication.assert_not_called()
    assert session.tools is original_tools and not session.tools_known
    assert session.tools_revision == 0
