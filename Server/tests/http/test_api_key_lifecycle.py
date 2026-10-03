"""Server shutdown closes its exact validator despite other cleanup failures."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from fastmcp import FastMCP
from services.api_key_service import ApiKeyService


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "hub", "pool"])
@pytest.mark.parametrize("replacement", [False, True])
async def test_lifespan_closes_captured_validator_after_other_cleanup(monkeypatch, failure, replacement):
    import main

    actual_client = httpx.AsyncClient
    clients = []

    def factory(**kwargs):
        client = actual_client(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={"valid": True, "user_id": "owned-user"})), trust_env=False, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    service = ApiKeyService("https://owned.invalid/validate")
    monkeypatch.setattr(ApiKeyService, "_instance", service)
    assert (await service.validate("original-owned")).valid
    shutdown = AsyncMock(side_effect=RuntimeError("hub cleanup") if failure == "hub" else None)
    disconnect = Mock(side_effect=RuntimeError("pool cleanup") if failure == "pool" else None)
    monkeypatch.setattr(main.PluginHub, "shutdown", shutdown)
    monkeypatch.setattr(main, "_plugin_registry", object())
    monkeypatch.setattr(main, "_unity_connection_pool", SimpleNamespace(disconnect_all=disconnect))
    monkeypatch.setenv("UNITY_MCP_SKIP_STARTUP_CONNECT", "1")
    timer = Mock()
    monkeypatch.setattr(main.threading, "Timer", Mock(return_value=timer))
    other = None
    try:
        try:
            async with main.server_lifespan(FastMCP("validator-lifecycle")):
                if replacement:
                    other = ApiKeyService("https://other-owned.invalid/validate")
                    assert (await other.validate("replacement-owned")).valid
        except RuntimeError as exc:
            assert failure is not None and str(exc) == failure + " cleanup"
        else:
            assert failure is None
        assert clients[0].is_closed
        assert not (await service.validate("after-shutdown")).valid
        shutdown.assert_awaited_once()
        disconnect.assert_called_once()
        timer.cancel.assert_called_once()
        if replacement:
            assert ApiKeyService.get_instance() is other
            assert not clients[1].is_closed
            assert (await other.validate("replacement-owned")).valid
        assert main._plugin_registry is None and main._unity_connection_pool is None
    finally:
        await service.aclose()
        if other is not None:
            await other.aclose()
