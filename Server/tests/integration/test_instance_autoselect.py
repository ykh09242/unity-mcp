import pytest

import transport.legacy.unity_connection as unity_connection
import transport.unity_instance_middleware as instance_middleware
from types import SimpleNamespace

from .test_helpers import DummyContext
from core.config import config


class DummyMiddlewareContext:
    def __init__(self, ctx):
        self.fastmcp_context = ctx


@pytest.mark.asyncio
async def test_auto_selects_single_instance_via_pluginhub(monkeypatch):
    class PluginHub:
        @classmethod
        def is_configured(cls) -> bool:
            return True

        @classmethod
        async def get_sessions(cls):
            raise AssertionError("get_sessions should be stubbed in test")

    monkeypatch.setattr(instance_middleware, "PluginHub", PluginHub)

    from transport.unity_instance_middleware import (
        UnityInstanceMiddleware,
        PluginHub as ImportedPluginHub,
    )

    assert ImportedPluginHub is PluginHub

    monkeypatch.setattr(config, "transport_mode", "http")

    middleware = UnityInstanceMiddleware()
    ctx = DummyContext()
    ctx.client_id = "client-1"
    middleware_context = DummyMiddlewareContext(ctx)

    call_count = {"sessions": 0}

    async def fake_get_sessions():
        call_count["sessions"] += 1
        return SimpleNamespace(
            sessions={
                "session-1": SimpleNamespace(project="Ramble", hash="deadbeef"),
            }
        )

    monkeypatch.setattr(PluginHub, "get_sessions", fake_get_sessions)

    selected = await middleware._maybe_autoselect_instance(ctx)

    assert selected == "Ramble@deadbeef"
    assert await middleware.get_active_instance(ctx) == "Ramble@deadbeef"
    assert call_count["sessions"] == 1

    await middleware._inject_unity_instance(middleware_context)

    assert await ctx.get_state("unity_instance") == "Ramble@deadbeef"
    assert call_count["sessions"] == 1


@pytest.mark.asyncio
async def test_auto_selects_single_instance_via_stdio(monkeypatch):
    class PluginHub:
        @classmethod
        def is_configured(cls) -> bool:
            return False

    monkeypatch.setattr(instance_middleware, "PluginHub", PluginHub)

    from transport.unity_instance_middleware import (
        UnityInstanceMiddleware,
        PluginHub as ImportedPluginHub,
    )

    assert ImportedPluginHub is PluginHub

    monkeypatch.setattr(config, "transport_mode", "stdio")

    middleware = UnityInstanceMiddleware()
    ctx = DummyContext()
    ctx.client_id = "client-1"
    middleware_context = DummyMiddlewareContext(ctx)

    class PoolStub:
        def discover_all_instances(self, force_refresh=False):
            assert force_refresh is True
            return [SimpleNamespace(id="UnityMCPTests@cc8756d4")]

    monkeypatch.setattr(unity_connection, "get_unity_connection_pool", lambda: PoolStub())

    selected = await middleware._maybe_autoselect_instance(ctx)

    assert selected == "UnityMCPTests@cc8756d4"
    assert await middleware.get_active_instance(ctx) == "UnityMCPTests@cc8756d4"

    await middleware._inject_unity_instance(middleware_context)

    assert await ctx.get_state("unity_instance") == "UnityMCPTests@cc8756d4"


@pytest.mark.asyncio
async def test_auto_select_handles_stdio_errors(monkeypatch):
    class PluginHub:
        @classmethod
        def is_configured(cls) -> bool:
            return False

    monkeypatch.setattr(instance_middleware, "PluginHub", PluginHub)

    from transport.unity_instance_middleware import (
        UnityInstanceMiddleware,
        PluginHub as ImportedPluginHub,
    )

    assert ImportedPluginHub is PluginHub

    middleware = UnityInstanceMiddleware()
    ctx = DummyContext()
    ctx.client_id = "client-1"

    class PoolStub:
        def discover_all_instances(self, force_refresh=False):
            raise ConnectionError("stdio unavailable")

    monkeypatch.setattr(unity_connection, "get_unity_connection_pool", lambda: PoolStub())

    selected = await middleware._maybe_autoselect_instance(ctx)

    assert selected is None
    assert await middleware.get_active_instance(ctx) is None
