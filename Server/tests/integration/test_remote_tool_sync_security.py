from unittest.mock import AsyncMock

import pytest

from core.config import config
from services.tools import sync_tool_visibility_from_unity
from services.tools.manage_tools import manage_tools
from transport.legacy import unity_connection


@pytest.mark.asyncio
async def test_remote_sync_never_queries_host_catalog(monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    legacy = AsyncMock(side_effect=AssertionError("Host catalog access"))
    monkeypatch.setattr(unity_connection, "async_send_command_with_retry", legacy)
    direct = await sync_tool_visibility_from_unity("HostProject@hash")
    assert direct["remote_sync_disabled"]
    result = await manage_tools(AsyncMock(), action="sync")
    assert "remote-hosted" in result["error"]
    legacy.assert_not_called()
