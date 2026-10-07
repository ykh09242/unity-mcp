"""Selected remote tool permission uses current atomic membership, not catalogs."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client, Context, FastMCP
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from starlette.websockets import WebSocketState

from core.config import config
from models.models import ToolDefinitionModel
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry
from transport.unity_instance_middleware import UnityInstanceMiddleware


@pytest.fixture
def remote_hub(monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", True)
    monkeypatch.setattr(config, "transport_mode", "http")
    registry = PluginRegistry()
    monkeypatch.setattr(PluginHub, "_registry", registry)
    monkeypatch.setattr(PluginHub, "_lock", asyncio.Lock())
    monkeypatch.setattr(PluginHub, "_connections", {})
    monkeypatch.setattr(
        "transport.unity_instance_middleware.get_registered_tools",
        lambda: [
            {"name": "direct", "unity_target": "direct"},
            {"name": "alias", "unity_target": "direct"},
            {"name": "helper", "unity_target": None},
        ],
    )
    return registry


async def connected(registry, sid="alice", owner="alice", generation="owned-generation"):
    await registry.register(sid, "Project", "hash", "test", user_id=owner)
    websocket = SimpleNamespace(
        state=SimpleNamespace(
            user_id=owner,
            plugin_generation=generation,
            plugin_registered=True,
            plugin_session_id=sid,
        ),
        client_state=WebSocketState.CONNECTED,
        application_state=WebSocketState.CONNECTED,
    )
    PluginHub._connections[sid] = websocket
    return websocket


def context_for(name, instance="Project@hash"):
    state = {"mcpforunity.active_instance": instance}

    async def set_state(key, value, **kwargs):
        state[key] = value

    context = SimpleNamespace(
        message=SimpleNamespace(name=name, arguments={}),
        fastmcp_context=SimpleNamespace(
            get_state=AsyncMock(side_effect=state.get), set_state=set_state
        ),
    )
    return context


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,registered",
    [("direct", "direct"), ("alias", "direct"), ("alias", "alias"), ("custom", "custom")],
)
async def test_selected_remote_call_does_not_materialize_session_or_tool_catalog(
    remote_hub, monkeypatch, name, registered
):
    await connected(remote_hub)
    await remote_hub.register_tools_for_session("alice", [ToolDefinitionModel(name=registered)])
    middleware = UnityInstanceMiddleware()
    monkeypatch.setattr(middleware, "_resolve_user_id", AsyncMock(return_value="alice"))
    listing = AsyncMock(side_effect=AssertionError("selected permission must not build a catalog"))
    monkeypatch.setattr(PluginHub, "get_sessions", listing)
    monkeypatch.setattr(PluginHub, "get_tools_for_project", listing)
    handler = AsyncMock(return_value="owned")
    assert await middleware.on_call_tool(context_for(name), handler) == "owned"
    handler.assert_awaited_once()
    listing.assert_not_awaited()


@pytest.mark.asyncio
async def test_membership_observes_toggle_immediately_and_preserves_server_only(
    remote_hub, monkeypatch
):
    await connected(remote_hub)
    middleware = UnityInstanceMiddleware()
    monkeypatch.setattr(middleware, "_resolve_user_id", AsyncMock(return_value="alice"))
    handler = AsyncMock(return_value="owned")
    await remote_hub.register_tools_for_session("alice", [ToolDefinitionModel(name="direct")])
    assert await middleware.on_call_tool(context_for("alias"), handler) == "owned"
    await remote_hub.register_tools_for_session("alice", [])
    with pytest.raises(ValueError, match="disabled or unavailable"):
        await middleware.on_call_tool(context_for("alias"), handler)
    assert await middleware.on_call_tool(context_for("helper"), handler) == "owned"
    assert handler.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["generation", "principal", "socket", "replacement", "toggle"])
async def test_captured_identity_failure_never_falls_back_to_new_catalog(
    remote_hub, monkeypatch, change
):
    websocket = await connected(remote_hub)
    await remote_hub.register_tools_for_session("alice", [ToolDefinitionModel(name="direct")])
    middleware = UnityInstanceMiddleware()
    monkeypatch.setattr(middleware, "_resolve_user_id", AsyncMock(return_value="alice"))
    original_capture = PluginHub.capture_tool_identity

    async def capture(session_id):
        identity = await original_capture(session_id)
        if change == "generation":
            websocket.state.plugin_generation = "replacement-generation"
        elif change == "principal":
            websocket.state.user_id = "bob"
        elif change == "socket":
            await connected(remote_hub)
        elif change == "replacement":
            await connected(remote_hub, sid="replacement")
            await remote_hub.register_tools_for_session(
                "replacement", [ToolDefinitionModel(name="direct")]
            )
        else:
            await remote_hub.register_tools_for_session("alice", [])
        return identity

    monkeypatch.setattr(PluginHub, "capture_tool_identity", capture)
    fallback = AsyncMock(return_value={"direct"})
    monkeypatch.setattr(middleware, "_resolve_enabled_tool_names_for_context", fallback)
    handler = AsyncMock()
    with pytest.raises(ValueError, match="disabled or unavailable"):
        await middleware.on_call_tool(context_for("direct"), handler)
    handler.assert_not_awaited()
    fallback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method",
    ["on_read_resource", "on_list_resources", "on_list_resource_templates", "on_list_tools"],
)
async def test_resource_and_listing_requests_do_not_capture_tool_identity(
    remote_hub, monkeypatch, method
):
    await connected(remote_hub)
    middleware = UnityInstanceMiddleware()
    monkeypatch.setattr(middleware, "_resolve_user_id", AsyncMock(return_value="alice"))
    capture = AsyncMock(side_effect=AssertionError("only tool calls require a permission identity"))
    monkeypatch.setattr(PluginHub, "capture_tool_identity", capture)
    assert (
        await getattr(middleware, method)(context_for("direct"), AsyncMock(return_value=[])) == []
    )
    capture.assert_not_awaited()


@pytest.mark.asyncio
async def test_atomic_registry_membership_checks_principal_mapping_and_session(remote_hub):
    await connected(remote_hub)
    await remote_hub.register_tools_for_session("alice", [ToolDefinitionModel(name="direct")])
    assert await remote_hub.has_tool_for_session("alice", "hash", "alice", ("direct",))
    assert not await remote_hub.has_tool_for_session("alice", "hash", "bob", ("direct",))
    assert not await remote_hub.has_tool_for_session("alice", "foreign", "alice", ("direct",))
    await connected(remote_hub, sid="replacement")
    await remote_hub.register_tools_for_session("replacement", [ToolDefinitionModel(name="direct")])
    assert not await remote_hub.has_tool_for_session("alice", "hash", "alice", ("direct",))


@pytest.mark.asyncio
async def test_no_live_captured_identity_preserves_existing_fallback(remote_hub, monkeypatch):
    await remote_hub.register("alice", "Project", "hash", "test", user_id="alice")
    await remote_hub.register_tools_for_session("alice", [ToolDefinitionModel(name="direct")])
    middleware = UnityInstanceMiddleware()
    monkeypatch.setattr(middleware, "_resolve_user_id", AsyncMock(return_value="alice"))
    fallback = AsyncMock(wraps=middleware._resolve_enabled_tool_names_for_context)
    monkeypatch.setattr(middleware, "_resolve_enabled_tool_names_for_context", fallback)
    assert (
        await middleware.on_call_tool(context_for("direct"), AsyncMock(return_value="fallback"))
        == "fallback"
    )
    fallback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_real_sdk_selected_calls_use_membership_but_listing_stays_complete(
    remote_hub, monkeypatch, mode
):
    await connected(remote_hub)
    await remote_hub.register_tools_for_session("alice", [ToolDefinitionModel(name="direct")])
    middleware = UnityInstanceMiddleware()
    monkeypatch.setattr(middleware, "_resolve_user_id", AsyncMock(return_value="alice"))
    server = FastMCP("owned-selected-membership")
    server.add_middleware(middleware)

    @server.tool
    async def helper(ctx: Context) -> str:
        await middleware.set_active_instance(ctx, "Project@hash")
        return "selected"

    @server.tool
    async def alias() -> str:
        return "owned"

    async with Client(server, mode=mode) as client:
        assert (await client.call_tool("helper")).data == "selected"
        listing = AsyncMock(wraps=PluginHub.get_sessions)
        monkeypatch.setattr(PluginHub, "get_sessions", listing)
        arguments = {"unity_instance": "Project@hash"} if mode == "auto" else {}
        assert (await client.call_tool("alias", arguments)).data == "owned"
        # Modern stateless calls use an explicit selector: its existing fresh
        # selector validation lists once; permission adds no second catalog.
        assert listing.await_count == (1 if mode == "auto" else 0)
        assert {tool.name for tool in await client.list_tools()} == {"helper", "alias"}
        assert listing.await_count == (2 if mode == "auto" else 1)
        await remote_hub.register_tools_for_session("alice", [])
        with pytest.raises((ToolError, MCPError)):
            await client.call_tool("alias", arguments)


@pytest.mark.asyncio
async def test_membership_waiting_for_registry_lock_cannot_authorize_after_reconfigure(
    remote_hub, monkeypatch
):
    await connected(remote_hub)
    await remote_hub.register_tools_for_session("alice", [ToolDefinitionModel(name="direct")])
    identity = await PluginHub._read_identity("alice")
    entered = asyncio.Event()
    original = remote_hub.has_tool_for_session

    async def membership(*args):
        entered.set()
        return await original(*args)

    monkeypatch.setattr(remote_hub, "has_tool_for_session", membership)
    # Restore every owner that real configure replaces; all other maps belong
    # to remote_hub, and configure does not mutate the old cached owners.
    for name in (
        "_mcp",
        "_loop",
        "_lock",
        "_editor_states",
        "_readiness_reads",
        "_ordinary_state_reads",
        "_large_results",
        "_raw_results",
    ):
        monkeypatch.setattr(PluginHub, name, getattr(PluginHub, name))
    task = None
    try:
        async with remote_hub._lock:
            task = asyncio.create_task(
                PluginHub.has_tool_for_identity(identity, "hash", "alice", ("direct",))
            )
            await entered.wait()
            PluginHub.configure(PluginRegistry())
        assert not await task
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_lost_required_live_capture_cannot_fall_back_after_replacement(
    remote_hub, monkeypatch
):
    await connected(remote_hub)
    await remote_hub.register_tools_for_session("alice", [ToolDefinitionModel(name="direct")])
    owned_lock = asyncio.Lock()
    monkeypatch.setattr(PluginHub, "_lock", owned_lock)
    middleware = UnityInstanceMiddleware()
    monkeypatch.setattr(middleware, "_resolve_user_id", AsyncMock(return_value="alice"))
    entered = asyncio.Event()
    original = PluginHub._read_identity

    async def capture(*args, **kwargs):
        entered.set()
        return await original(*args, **kwargs)

    monkeypatch.setattr(PluginHub, "_read_identity", capture)
    fallback = AsyncMock(return_value={"direct"})
    monkeypatch.setattr(middleware, "_resolve_enabled_tool_names_for_context", fallback)
    handler = AsyncMock()
    task = None
    try:
        async with owned_lock:
            task = asyncio.create_task(middleware.on_call_tool(context_for("direct"), handler))
            await entered.wait()
            await connected(remote_hub, sid="replacement")
            await remote_hub.register_tools_for_session(
                "replacement", [ToolDefinitionModel(name="direct")]
            )
            PluginHub._connections.pop("alice")
        with pytest.raises(ValueError, match="disabled or unavailable"):
            await task
        handler.assert_not_awaited()
        fallback.assert_not_awaited()
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
