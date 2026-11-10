"""Static inventories remain available while a persisted Editor selection is offline."""

import asyncio
from types import SimpleNamespace

import pytest

from tests.integration.test_helpers import DummyContext
from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from transport.unity_instance_middleware import UnityInstanceMiddleware


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "inventory", ["on_list_tools", "on_list_resources", "on_list_resource_templates"]
)
async def test_inventory_does_not_wait_for_disconnected_persisted_selection(
    command_hub, monkeypatch, inventory
):
    # Given: a real registry loses the Editor selected by this stateful client.
    monkeypatch.setenv("UNITY_MCP_SESSION_RESOLVE_MAX_WAIT_S", "300")
    middleware = UnityInstanceMiddleware()
    ctx = DummyContext()
    ctx.session = SimpleNamespace(client_capabilities=None)
    await middleware.set_active_instance(ctx, "Fixture@hash")
    await command_hub.registry.unregister("session")
    waits = []
    wait_for_change = command_hub.registry.wait_for_change

    async def observe_wait(changed, timeout):
        waits.append(timeout)
        return await wait_for_change(changed, timeout)

    monkeypatch.setattr(command_hub.registry, "wait_for_change", observe_wait)
    entered = []

    async def catalog(context):
        entered.append(context)
        return []

    # When: each real inventory middleware is called without a tool deadline.
    context = SimpleNamespace(fastmcp_context=ctx, message=SimpleNamespace())
    result = await asyncio.wait_for(getattr(middleware, inventory)(context, catalog), timeout=1)

    # Then: static enumeration reaches its handler without reconnect waiting or dispatch.
    assert result == [] and entered == [context]
    assert waits == [] and command_hub.sent == []
    assert await middleware.get_active_instance(ctx) == "Fixture@hash"
    assert await ctx.get_state("unity_session_id") is None
