import pytest

import transport.legacy.unity_connection as unity_connection
import transport.unity_instance_middleware as instance_middleware
from types import SimpleNamespace

from .test_helpers import DummyContext
from core.config import config
from fastmcp.exceptions import ToolError
from services.resources.editor_state import infer_single_instance_id
from services.tools import manage_scene
from unittest.mock import AsyncMock


class DummyMiddlewareContext:
    def __init__(self, ctx):
        self.fastmcp_context = ctx


@pytest.mark.asyncio
@pytest.mark.parametrize("selector", ["Main", "dead", "Main@deadbeef"])
async def test_stdio_default_survives_an_offline_editor_and_explicit_overrides(
    monkeypatch, selector
):
    monkeypatch.setattr(config, "transport_mode", "stdio")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "require_explicit_target", False)
    monkeypatch.setenv("UNITY_MCP_DEFAULT_INSTANCE", selector)
    pool = unity_connection.UnityConnectionPool()
    monkeypatch.setattr(unity_connection, "get_unity_connection_pool", lambda: pool)
    main = SimpleNamespace(id="Main@deadbeef", hash="deadbeef", name="Main", port=6400, path="a")
    other = SimpleNamespace(id="Other@cafebabe", hash="cafebabe", name="Other", port=6401, path="b")
    inventory = [other]
    discovery = unity_connection.PortDiscovery
    monkeypatch.setattr(discovery, "discover_all_unity_instances", lambda: inventory.copy())
    monkeypatch.setattr(
        discovery,
        "discover_unity_instance",
        lambda target: next((i for i in inventory if i.id == target), None),
    )
    hub = instance_middleware.PluginHub
    monkeypatch.setattr(hub, "is_configured", lambda: True)
    sessions = SimpleNamespace(
        sessions={"other": SimpleNamespace(project=other.name, hash=other.hash)}
    )
    monkeypatch.setattr(hub, "get_sessions", AsyncMock(return_value=sessions))

    mutations = []

    async def send(command, params, *, instance_id=None):
        target = pool.resolve_instance(instance_id)
        mutations.append((params["action"], target.id))
        return {"success": True}

    monkeypatch.setattr(manage_scene, "async_send_command_with_retry", send)
    middleware = instance_middleware.UnityInstanceMiddleware()
    ctx = DummyContext()

    async def inject(override=None, *, require_explicit=False):
        arguments = {"unity_instance": override} if override else {}
        request = SimpleNamespace(fastmcp_context=ctx, message=SimpleNamespace(arguments=arguments))
        await middleware._inject_unity_instance(request, require_explicit=require_explicit)
        return await ctx.get_state("unity_instance")

    assert await inject() is None
    assert await infer_single_instance_id(ctx) is None
    assert not (await manage_scene.manage_scene(ctx, "save"))["success"]
    assert mutations == []
    assert await middleware.get_active_instance(ctx) is None

    assert await inject(other.id) == other.id
    assert (await manage_scene.manage_scene(ctx, "save"))["success"]
    inventory.append(main)
    assert await inject() == main.id
    assert await infer_single_instance_id(ctx) == main.id
    assert (await manage_scene.manage_scene(ctx, "save"))["success"]
    assert mutations == [("save", other.id), ("save", main.id)]
    assert await middleware.get_active_instance(ctx) is None

    monkeypatch.setattr(config, "require_explicit_target", True)
    assert await inject() == main.id
    with pytest.raises(ToolError, match="explicit_unity_target_required"):
        await inject(require_explicit=True)
    await middleware.set_active_instance(ctx, other.id)
    assert await inject(require_explicit=True) == other.id
    assert await inject(main.id, require_explicit=True) == main.id
    assert await inject(require_explicit=True) == other.id


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
        default_instance_id = None

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
        default_instance_id = None

        def discover_all_instances(self, force_refresh=False):
            raise ConnectionError("stdio unavailable")

    monkeypatch.setattr(unity_connection, "get_unity_connection_pool", lambda: PoolStub())

    selected = await middleware._maybe_autoselect_instance(ctx)

    assert selected is None
    assert await middleware.get_active_instance(ctx) is None
