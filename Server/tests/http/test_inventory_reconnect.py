"""Static inventories remain available while a persisted Editor selection is offline."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client, Context, FastMCP

from core.config import config
from services.credential_lease import bind_credential_lease, CredentialLeaseError
from services.resources.unity_instances import unity_instances
from services.tools.set_active_instance import set_active_instance
from tests.http.test_remote_permissions import permission_service
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


@pytest.mark.asyncio
async def test_read_instances_on_a_stateful_client_with_an_offline_selection(
    command_hub, monkeypatch
):
    monkeypatch.setenv("UNITY_MCP_SESSION_RESOLVE_MAX_WAIT_S", "300")
    app = FastMCP("inventory")
    middleware = UnityInstanceMiddleware()
    monkeypatch.setattr(
        "transport.unity_instance_middleware._unity_instance_middleware", middleware
    )
    app.add_middleware(middleware)
    app.resource("mcpforunity://instances")(unity_instances)
    app.tool(name="set_active_instance")(set_active_instance)

    @app.resource("mcpforunity://selected-project")
    async def selected_project(ctx: Context) -> str:
        return await ctx.get_state("unity_instance")

    wait_for_change = AsyncMock(return_value=False)
    monkeypatch.setattr(command_hub.registry, "wait_for_change", wait_for_change)
    async with Client(app, mode="legacy") as client:
        selected = await client.call_tool("set_active_instance", {"instance": "Fixture@hash"})
        assert selected.data["success"]
        await command_hub.registry.unregister("session")
        await command_hub.registry.register("second", "Second", "other", "6000.3")
        result = await client.read_resource("mcpforunity://instances")
        assert [row["id"] for row in json.loads(result[0].text)["instances"]] == ["Second@other"]
        wait_for_change.assert_not_awaited()
        scoped = await client.read_resource("mcpforunity://selected-project")
        assert scoped[0].text == "Fixture@hash"
        wait_for_change.assert_awaited_once()
    assert command_hub.sent == []


@pytest.mark.asyncio
async def test_read_instances_checks_authority_and_ignores_stale_project_and_principal(
    command_hub, monkeypatch
):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setenv("UNITY_MCP_DEFAULT_INSTANCE", "Private@private")
    await command_hub.registry.register("owned", "Second", "other", "6000.3", user_id="alice")
    await command_hub.registry.register("hidden", "Private", "private", "6000.3", user_id="bob")
    ctx = DummyContext()
    await ctx.set_state("user_id", "bob")
    middleware = UnityInstanceMiddleware()
    await middleware.set_active_instance(ctx, "Offline@offline")
    context = SimpleNamespace(
        fastmcp_context=ctx, message=SimpleNamespace(uri="mcpforunity://instances")
    )

    async def read_resource(request):
        return await unity_instances(request.fastmcp_context)

    with bind_credential_lease(None), pytest.raises(CredentialLeaseError):
        await middleware.on_read_resource(context, read_resource)
    async with permission_service(
        monkeypatch, {"reader": {"unity_mcp_permissions": ["read"]}}
    ) as auth:
        lease = (await auth.service.validate("reader")).lease
        with bind_credential_lease(lease):
            result = await middleware.on_read_resource(context, read_resource)
    assert [row["id"] for row in result["instances"]] == ["Second@other"]
    assert await middleware.get_active_instance(ctx) == "Offline@offline"
    assert await ctx.get_state("unity_session_id") is None
    assert await ctx.get_state("user_id") == "alice"
