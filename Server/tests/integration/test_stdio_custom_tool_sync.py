"""
Tests for stdio-mode custom tool discovery (GitHub issue #837).

Verifies that:
1. sync_tool_visibility_from_unity registers custom tools when extended metadata is present
2. Custom tools are skipped gracefully when metadata is missing (old Unity package)
3. Reconnection flag triggers a background re-sync
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from transport.legacy.unity_connection import UnityConnection


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_unity_response(tools, include_extended=True):
    """Build a fake get_tool_states response from Unity."""
    tool_list = []
    for t in tools:
        entry = {
            "name": t["name"],
            "group": t.get("group", "core"),
            "enabled": t.get("enabled", True),
        }
        if include_extended:
            entry.update(
                {
                    "description": t.get("description", f"Tool: {t['name']}"),
                    "auto_register": t.get("auto_register", True),
                    "is_built_in": t.get("is_built_in", True),
                    "structured_output": t.get("structured_output", False),
                    "requires_polling": t.get("requires_polling", False),
                    "poll_action": t.get("poll_action", "status"),
                    "max_poll_seconds": t.get("max_poll_seconds", 0),
                    "parameters": t.get("parameters", []),
                }
            )
        tool_list.append(entry)
    return {
        "data": {
            "tools": tool_list,
            "groups": [],
        }
    }


BUILTIN_TOOL = {
    "name": "manage_gameobject",
    "group": "core",
    "is_built_in": True,
    "description": "Manage GameObjects in the scene.",
}

CUSTOM_TOOL = {
    "name": "test_ping",
    "group": "core",
    "is_built_in": False,
    "description": "Simple test tool that returns a pong.",
    "parameters": [
        {
            "name": "message",
            "description": "Message to echo",
            "type": "string",
            "required": False,
            "default_value": "pong",
        },
    ],
}


def _captured_reply(response):
    async def reply(*_args, **kwargs):
        kwargs["_catalog_capture"].append(
            (
                SimpleNamespace(instance_id="Project@deadbeef"),
                "authenticated-fixture",
            )
        )
        return response

    return reply


@pytest.fixture(autouse=True)
def authenticated_catalog_context(monkeypatch):
    from core.config import config
    from services.custom_tool_service import CustomToolService
    from transport.plugin_hub import PluginHub

    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(CustomToolService, "_instance", None)
    monkeypatch.setattr(PluginHub, "_mcp", None)
    monkeypatch.setattr(
        "transport.legacy.unity_connection._commit_stdio_catalog",
        lambda _conn, _generation, publish: publish(),
    )
    monkeypatch.setattr(
        "transport.legacy.unity_connection.get_authenticated_stdio_generation",
        AsyncMock(return_value="authenticated-fixture"),
    )


# ---------------------------------------------------------------------------
# sync_tool_visibility_from_unity — custom tool registration
# ---------------------------------------------------------------------------


@pytest.fixture
def catalog_service(monkeypatch):
    from fastmcp import FastMCP
    from services.custom_tool_service import CustomToolService
    import services.registry.tool_registry as registry

    monkeypatch.setattr(
        registry,
        "_tool_registry",
        [{"name": "manage_gameobject", "unity_target": "manage_gameobject", "group": "core"}],
    )
    return CustomToolService(FastMCP("stdio-sync"))


@pytest.mark.asyncio
async def test_sync_registers_custom_tools(catalog_service):
    response = _make_unity_response([BUILTIN_TOOL, CUSTOM_TOOL])
    with patch(
        "transport.legacy.unity_connection.async_send_command_with_retry",
        new_callable=AsyncMock,
        side_effect=_captured_reply(response),
    ):
        from services.tools import sync_tool_visibility_from_unity

        result = await sync_tool_visibility_from_unity(notify=False)
    assert result["synced"] and result["custom_tool_count"] == 1
    source = catalog_service._sources[("stdio", None, "deadbeef")]
    assert set(source.tools) == {"manage_gameobject", "test_ping"}
    assert source.tools["test_ping"].parameters[0].name == "message"
    assert set(catalog_service._owned_tools) == {"test_ping"}


@pytest.mark.asyncio
async def test_sync_keeps_builtin_availability_without_custom_registration(catalog_service):
    with patch(
        "transport.legacy.unity_connection.async_send_command_with_retry",
        new_callable=AsyncMock,
        side_effect=_captured_reply(_make_unity_response([BUILTIN_TOOL])),
    ):
        from services.tools import sync_tool_visibility_from_unity

        result = await sync_tool_visibility_from_unity(notify=False)
    assert result["synced"] and result["custom_tool_count"] == 0
    assert catalog_service.get_stdio_availability("deadbeef", "authenticated-fixture") == {
        "manage_gameobject"
    }
    assert not catalog_service._owned_tools


@pytest.mark.asyncio
async def test_sync_without_provenance_keeps_existing_catalog(catalog_service):
    response = _make_unity_response([BUILTIN_TOOL, CUSTOM_TOOL], include_extended=False)
    with patch(
        "transport.legacy.unity_connection.async_send_command_with_retry",
        new_callable=AsyncMock,
        return_value=response,
    ):
        from services.tools import sync_tool_visibility_from_unity

        result = await sync_tool_visibility_from_unity(notify=False)
    assert result["synced"] and result["custom_tool_count"] == 0
    assert result["settings_sync"]["status"] == "unconfirmed"
    assert not catalog_service._sources and not catalog_service._owned_tools


@pytest.mark.asyncio
async def test_sync_handles_custom_tool_service_not_initialized():
    with patch(
        "transport.legacy.unity_connection.async_send_command_with_retry",
        new_callable=AsyncMock,
        side_effect=_captured_reply(_make_unity_response([CUSTOM_TOOL])),
    ):
        from services.tools import sync_tool_visibility_from_unity

        result = await sync_tool_visibility_from_unity(notify=False)
    assert result == {"error": "Unity catalog service unavailable"}


# ---------------------------------------------------------------------------
# Reconnection re-sync trigger
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconnection_flag_triggers_resync():
    """After reconnection, async_send_command_with_retry should schedule a re-sync."""
    mock_conn = UnityConnection(port=6400, instance_id="Project@deadbeef")
    mock_conn._needs_tool_resync = True

    with (
        patch(
            "transport.legacy.unity_connection._send_command_with_retry",
            return_value=({"success": True, "message": "ok"}, mock_conn),
        ),
        patch(
            "transport.legacy.unity_connection.get_unity_connection",
            return_value=mock_conn,
        ),
        patch(
            "transport.legacy.unity_connection._resync_tools_after_reconnect",
            new_callable=AsyncMock,
        ) as mock_resync,
    ):
        from transport.legacy.unity_connection import async_send_command_with_retry

        result = await async_send_command_with_retry("manage_gameobject", {"action": "list"})

        # ensure_future schedules on the event loop; give it a tick to run
        await asyncio.sleep(0)

    assert result["success"] is True
    # Flag should be cleared
    assert mock_conn._needs_tool_resync is False
    # Re-sync should have been scheduled
    mock_resync.assert_awaited_once_with("Project@deadbeef")


@pytest.mark.asyncio
async def test_no_resync_for_get_tool_states():
    """get_tool_states itself should NOT trigger re-sync (avoids recursion)."""
    mock_conn = UnityConnection(port=6400, instance_id="Project@deadbeef")
    mock_conn._needs_tool_resync = True

    with (
        patch(
            "transport.legacy.unity_connection._send_command_with_retry",
            return_value=({"data": {"tools": []}}, mock_conn),
        ),
        patch(
            "transport.legacy.unity_connection.get_unity_connection",
            return_value=mock_conn,
        ),
        patch(
            "transport.legacy.unity_connection._resync_tools_after_reconnect",
            new_callable=AsyncMock,
        ) as mock_resync,
    ):
        from transport.legacy.unity_connection import async_send_command_with_retry

        await async_send_command_with_retry("get_tool_states", {})

    # Flag should be cleared, but no re-sync task should be scheduled
    assert mock_conn._needs_tool_resync is False
    mock_resync.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_resync_when_not_reconnected():
    """When _needs_tool_resync is False, no re-sync should be scheduled."""
    mock_conn = UnityConnection(port=6400, instance_id="Project@deadbeef")
    mock_conn._needs_tool_resync = False

    with (
        patch(
            "transport.legacy.unity_connection._send_command_with_retry",
            return_value=({"success": True}, mock_conn),
        ),
        patch(
            "transport.legacy.unity_connection.get_unity_connection",
            return_value=mock_conn,
        ),
        patch(
            "transport.legacy.unity_connection._resync_tools_after_reconnect",
            new_callable=AsyncMock,
        ) as mock_resync,
    ):
        from transport.legacy.unity_connection import async_send_command_with_retry

        await async_send_command_with_retry("manage_gameobject", {"action": "list"})
        await asyncio.sleep(0)

    mock_resync.assert_not_awaited()
