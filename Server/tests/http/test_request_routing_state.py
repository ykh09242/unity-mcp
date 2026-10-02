"""Real FastMCP requests keep per-call Unity routing isolated."""

import asyncio  # noqa: ANYIO_OK -- synchronize actual concurrent MCP requests.
from typing import TypedDict
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastmcp import Client, Context, FastMCP

from core.config import config
from transport.plugin_hub import PluginHub
from transport.plugin_registry import PluginRegistry
from transport.unity_instance_middleware import UnityInstanceMiddleware


class RoutingSnapshot(TypedDict):
    instance: str | None
    session: str | None
    user: str | None


@pytest_asyncio.fixture
async def routing_server(monkeypatch: pytest.MonkeyPatch) -> FastMCP:
    monkeypatch.setattr(config, "transport_mode", "http")
    monkeypatch.setattr(config, "http_remote_hosted", False)
    registry = PluginRegistry()
    await registry.register("first", "First", "aaaa1111", "6000")
    await registry.register("second", "Second", "bbbb2222", "6000")
    monkeypatch.setattr(PluginHub, "_registry", registry)
    monkeypatch.setattr(PluginHub, "_lock", asyncio.Lock())
    monkeypatch.setattr(PluginHub, "_resolve_session_id", AsyncMock(side_effect=lambda instance, **kwargs: "first" if "aaaa1111" in instance else "second"))
    server = FastMCP("request-routing")
    server.add_middleware(UnityInstanceMiddleware())
    return server


@pytest.mark.asyncio
async def test_inline_override_is_visible_to_handler_but_does_not_pin_next_request(routing_server: FastMCP) -> None:
    # Given a real MCP session with two available plugins and no selected default.
    @routing_server.tool
    async def routing_probe(ctx: Context) -> RoutingSnapshot:
        return {"instance": await ctx.get_state("unity_instance"), "session": await ctx.get_state("unity_session_id"), "user": await ctx.get_state("user_id")}

    async with Client(routing_server) as client:
        first = await client.call_tool("routing_probe", {"unity_instance": "First@aaaa1111"})
        assert first.structured_content["instance"] == "First@aaaa1111"
        assert first.structured_content["session"] == "first"
        # When the next real request supplies no per-call target.
        following = await client.call_tool("routing_probe")
        # Then the handler cannot inherit the previous request's transient routing.
        assert following.structured_content["instance"] is None
        assert following.structured_content["session"] is None


@pytest.mark.asyncio
async def test_concurrent_inline_overrides_do_not_replace_each_others_routing(routing_server: FastMCP) -> None:
    # Given concurrent requests in one real MCP session.
    both_entered = asyncio.Event()
    arrivals = 0

    @routing_server.tool
    async def concurrent_probe(ctx: Context) -> RoutingSnapshot:
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            both_entered.set()
        await asyncio.wait_for(both_entered.wait(), timeout=2)
        return {"instance": await ctx.get_state("unity_instance"), "session": await ctx.get_state("unity_session_id"), "user": await ctx.get_state("user_id")}

    async with Client(routing_server) as client:
        # When both middleware writes finish before either handler reads state.
        first, second = await asyncio.gather(
            client.call_tool("concurrent_probe", {"unity_instance": "First@aaaa1111"}),
            client.call_tool("concurrent_probe", {"unity_instance": "Second@bbbb2222"}),
        )
        # Then each actual handler sees its own middleware's route.
        assert first.structured_content["instance"] == "First@aaaa1111"
        assert first.structured_content["session"] == "first"
        assert second.structured_content["instance"] == "Second@bbbb2222"
        assert second.structured_content["session"] == "second"


@pytest.mark.asyncio
async def test_persisted_active_selection_survives_a_per_call_override(routing_server: FastMCP) -> None:
    # Given a pinned default stored through the middleware's session API.
    middleware = UnityInstanceMiddleware()

    @routing_server.tool
    async def pin_route(ctx: Context) -> None:
        await middleware.set_active_instance(ctx, "First@aaaa1111")

    @routing_server.tool
    async def pinned_probe(ctx: Context) -> str | None:
        return await ctx.get_state("unity_instance")

    async with Client(routing_server) as client:
        await client.call_tool("pin_route")
        override = await client.call_tool("pinned_probe", {"unity_instance": "Second@bbbb2222"})
        assert override.data == "Second@bbbb2222"
        # When the following request omits a per-call override.
        following = await client.call_tool("pinned_probe")
        # Then the explicit session default remains selected.
        assert following.data == "First@aaaa1111"


@pytest.mark.asyncio
async def test_old_persisted_transient_values_are_shadowed_per_request(routing_server: FastMCP) -> None:
    # Given state persisted by a prior server version in the same MCP session.
    @routing_server.tool
    async def seed_old_state(ctx: Context) -> None:
        await ctx.set_state("unity_instance", "First@aaaa1111")
        await ctx.set_state("unity_session_id", "first")
        await ctx.set_state("user_id", "old-user")

    @routing_server.tool
    async def migration_probe(ctx: Context) -> RoutingSnapshot:
        return {"instance": await ctx.get_state("unity_instance"), "session": await ctx.get_state("unity_session_id"), "user": await ctx.get_state("user_id")}

    async with Client(routing_server) as client:
        await client.call_tool("seed_old_state")
        # When the next unpinned request goes through the actual middleware.
        result = await client.call_tool("migration_probe")
        # Then stale session values cannot become a routing or identity fallback.
        assert result.structured_content == {"instance": None, "session": None, "user": None}
