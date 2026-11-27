"""Static inventories remain available while a persisted Editor selection is offline."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client, Context, FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.middleware import Middleware

from core.config import config
from services.credential_lease import bind_credential_lease, CredentialLeaseError
from services.resources import register_all_resources
from services.resources.unity_instances import unity_instances
from services.tools.manage_tools import manage_tools
from services.tools.read_unity_resource import read_unity_resource
from services.tools.set_active_instance import set_active_instance
from tests.http.test_remote_permissions import permission_service
from tests.integration.test_helpers import DummyContext
from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from transport.unity_instance_middleware import UnityInstanceMiddleware


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_tools_only_editor_inventory_uses_the_connections_resource(command_hub, mode):
    from transport.response_limit_middleware import ResponseLimitMiddleware

    app = FastMCP("tools-only discovery")
    app.add_middleware(ResponseLimitMiddleware())
    app.add_middleware(UnityInstanceMiddleware())
    register_all_resources(app)
    app.tool(name="manage_tools")(manage_tools)
    app.tool(read_unity_resource)
    await command_hub.registry.register("second", "Second", "other", "6000.3")
    async with Client(app, mode=mode, cache=False) as client:
        # A host may expose callable tools while omitting its resource browser.
        tools = {item.name: item for item in await client.list_tools()}
        assert (
            "list_instances" in tools["manage_tools"].input_schema["properties"]["action"]["enum"]
        )
        result = (
            await client.call_tool("manage_tools", {"action": "list_instances"})
        ).structured_content
        resource = json.loads((await client.read_resource("mcpforunity://instances"))[0].text)
        assert result == resource and result["success"]
        for tool, arguments in (
            ("read_unity_resource", {"uri": "mcpforunity://instances"}),
            ("manage_tools", {"action": "read_resource", "uri": "mcpforunity://instances"}),
        ):
            observed = (await client.call_tool(tool, arguments)).structured_content
            assert json.loads(observed["contents"][0]["text"]) == resource
        assert {row["id"] for row in result["instances"]} == {"Fixture@hash", "Second@other"}
        scoped = await client.call_tool(
            "manage_tools",
            {"action": "list_instances", "unity_instance": "Fixture@hash"},
            raise_on_error=False,
        )
        assert scoped.is_error and "Second@other" not in str(scoped.content)
        await command_hub.registry.unregister("session")
        await command_hub.registry.unregister("second")
        empty = (
            await client.call_tool("manage_tools", {"action": "list_instances"})
        ).structured_content
        assert empty["success"] and empty["instance_count"] == 0 and empty["instances"] == []
        observed = await client.call_tool("read_unity_resource", {"uri": "mcpforunity://instances"})
        assert json.loads(observed.structured_content["contents"][0]["text"]) == empty
    assert command_hub.sent == []


@pytest.mark.asyncio
async def test_pushed_editor_state_keeps_native_read_grants_on_both_leases(
    command_hub, monkeypatch
):
    import time
    from services.operation_permissions import OperationPermissionError
    from tests.test_editor_state_store import message
    from transport.plugin_hub import PluginHub
    from transport.remote_auth_middleware import AUTHENTICATED_LEASE_STATE

    monkeypatch.setattr(config, "http_remote_hosted", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    socket = command_hub.websocket
    socket.state.user_id = "alice"
    socket.state.plugin_features = frozenset({"editor_state_v1"})
    now = int(time.time() * 1000)
    PluginHub._editor_states.register_session("session", "alice")
    assert PluginHub._editor_states.accept("session", message(observed=now))
    async with permission_service(
        monkeypatch,
        {
            "reader": {"unity_mcp_permissions": ["read"]},
            "editor": {"unity_mcp_permissions": ["edit"]},
        },
    ) as fixture:
        reader = (await fixture.service.validate("reader")).lease
        editor = (await fixture.service.validate("editor")).lease
        for requester, plugin in ((reader, reader), (reader, editor), (editor, reader)):
            setattr(socket.state, AUTHENTICATED_LEASE_STATE, plugin)
            with bind_credential_lease(requester):
                if requester is reader and plugin is reader:
                    state = await PluginHub.get_cached_editor_state("Fixture@hash", user_id="alice")
                    assert state["sequence"] == 1
                else:
                    with pytest.raises(OperationPermissionError):
                        await PluginHub.get_cached_editor_state("Fixture@hash", user_id="alice")
    assert command_hub.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_versioned_inventory_uses_the_callback_selected_by_the_sdk(command_hub, mode):
    app = FastMCP("versioned discovery")
    middleware = UnityInstanceMiddleware()
    app.add_middleware(middleware)
    register_all_resources(app)
    app.tool(name="manage_tools", version="1.0")(manage_tools)

    @app.tool(name="manage_tools", version="2.0")
    async def identify_version() -> str:
        return "2.0"

    await command_hub.registry.register("second", "Second", "other", "6000.3")
    async with Client(app, mode=mode, cache=False) as client:
        inventory = await client.call_tool(
            "manage_tools", {"action": "list_instances"}, version="1.0"
        )
        assert {row["id"] for row in inventory.structured_content["instances"]} == {
            "Fixture@hash",
            "Second@other",
        }
        restricted = await client.call_tool(
            "manage_tools",
            {"action": "list_instances", "unity_instance": "Fixture@hash"},
            version="1.0",
            raise_on_error=False,
        )
        assert restricted.is_error and "Second@other" not in str(restricted.content)
        unknown = await client.call_tool(
            "manage_tools", {"action": "list_instances"}, version="3.0", raise_on_error=False
        )
        assert unknown.is_error and "Fixture@hash" not in str(unknown.content)
    assert command_hub.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["hidden", "read_denied"])
async def test_editor_inventory_tool_preserves_resource_policy(command_hub, policy):
    class ResourcePolicy(Middleware):
        async def on_list_resources(self, context, call_next):
            entries = await call_next(context)
            return [] if policy == "hidden" else entries

        async def on_read_resource(self, context, call_next):
            if policy == "read_denied":
                raise AuthorizationError("Editor inventory denied")
            return await call_next(context)

    app = FastMCP("restricted discovery")
    app.add_middleware(UnityInstanceMiddleware())
    app.add_middleware(ResourcePolicy())
    register_all_resources(app)
    app.tool(name="manage_tools")(manage_tools)
    async with Client(app, mode="legacy", cache=False) as client:
        reply = await client.call_tool(
            "manage_tools", {"action": "list_instances"}, raise_on_error=False
        )
        assert reply.is_error and "Fixture@hash" not in str(reply.content)
    assert command_hub.sent == []


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
    register_all_resources(app)
    app.tool(name="set_active_instance")(set_active_instance)
    app.tool(name="manage_tools")(manage_tools)

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
        discovered = await client.call_tool("manage_tools", {"action": "list_instances"})
        assert discovered.structured_content == json.loads(result[0].text)
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


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_general_connection_inventory_needs_only_the_requesters_read_grant(
    command_hub, monkeypatch, mode
):
    from mcp.shared.exceptions import MCPError
    from fastmcp.exceptions import ToolError
    from transport.remote_auth_middleware import AUTHENTICATED_LEASE_STATE

    app = FastMCP("hosted instance discovery")
    middleware = UnityInstanceMiddleware()
    app.add_middleware(middleware)
    register_all_resources(app)
    app.tool(manage_tools)
    app.tool(set_active_instance)
    app.tool(read_unity_resource)
    monkeypatch.setattr(
        "transport.unity_instance_middleware._unity_instance_middleware", middleware
    )
    monkeypatch.setattr(config, "http_remote_hosted", True)
    await command_hub.registry.unregister("session")
    await command_hub.registry.register("session", "Fixture", "hash", "6000", user_id="alice")
    await command_hub.registry.register("private", "Other", "other", "6000", user_id="bob")
    grants = {
        "reader": {"unity_mcp_permissions": ["read"]},
        "editor": {"unity_mcp_permissions": ["edit"]},
    }
    async with permission_service(monkeypatch, grants) as fixture:
        reader = (await fixture.service.validate("reader")).lease
        editor = (await fixture.service.validate("editor")).lease
        command_hub.websocket.state.user_id = "alice"
        setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, reader)
        async with Client(app, mode=mode, cache=False) as client:
            if mode == "legacy":
                with bind_credential_lease(reader):
                    saved = await client.call_tool(
                        "set_active_instance", {"instance": "Fixture@hash"}
                    )
                    assert saved.structured_content["success"]
            setattr(command_hub.websocket.state, AUTHENTICATED_LEASE_STATE, editor)
            uri = "mcpforunity://instances"
            for name, args in (
                ("manage_tools", {"action": "read_resource", "uri": uri}),
                ("read_unity_resource", {"uri": uri}),
            ):
                with bind_credential_lease(reader):
                    enumerated = await client.call_tool(
                        "manage_tools", {"action": "list_instances"}
                    )
                    actual = await client.call_tool(name, args)
                    payload = json.loads(actual.structured_content["contents"][0]["text"])
                    assert payload == enumerated.structured_content
                    assert [row["id"] for row in payload["instances"]] == ["Fixture@hash"]
                    with pytest.raises((MCPError, ToolError)):
                        await client.call_tool(name, {**args, "unity_instance": "Other@other"})
                    with pytest.raises((MCPError, ToolError)):
                        await client.call_tool(
                            name,
                            {
                                **args,
                                "uri": "mcpforunity://menu-items",
                                "unity_instance": "Fixture@hash",
                            },
                        )
                with bind_credential_lease(editor), pytest.raises((MCPError, ToolError)):
                    await client.call_tool(name, args)
    assert command_hub.sent == []
