"""Standard discovery and normal calls on an authenticated loopback MCP transport."""

import asyncio
from contextlib import AsyncExitStack
from unittest.mock import AsyncMock

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from fastmcp.utilities.tests import run_server_async
from mcp.shared.exceptions import MCPError
from mcp.types import PaginatedRequestParams, ToolListChangedNotification
import pytest

from tests.http.test_discovery_profiles import discovery_server, names  # noqa: F401
from tests.http.test_catalog_selection import catalog_server, publish  # noqa: F401
from tests.timing import watchdog_timeout
from transport.unity_instance_middleware import UnityInstanceMiddleware


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_authenticated_refresh_notification_list_and_native_call(discovery_server, mode):
    build, routing = discovery_server
    server, _ = build(
        auth=StaticTokenVerifier(
            tokens={
                "first-test-token": {"client_id": "catalog-test", "sub": "first-owner"},
                "other-test-token": {"client_id": "catalog-test", "sub": "other-owner"},
            }
        )
    )
    received = asyncio.Event()

    async def handle(message):
        if isinstance(message, ToolListChangedNotification):
            received.set()

    async with run_server_async(server) as url, AsyncExitStack() as stack:

        def transport(token):
            return StreamableHttpTransport(url, headers={"Authorization": f"Bearer {token}"})

        client = await stack.enter_async_context(
            Client(transport("first-test-token"), mode=mode, cache=False, message_handler=handle)
        )
        other = await stack.enter_async_context(
            Client(transport("other-test-token"), mode=mode, cache=False)
        )
        initial = await names(client)
        assert "effect_probe" not in initial
        subscription = None
        if mode == "auto":
            subscription = await stack.enter_async_context(client.listen(tools_list_changed=True))
            assert subscription.honored.tools_list_changed
        activation = (
            await client.call_tool("manage_tools", {"action": "activate", "tool": "effect_probe"})
        ).structured_content
        assert activation["refresh_request"] == {"method": "tools/list", "params": {}}
        assert (
            activation["server_advertised"]
            and activation["client_inventory_status"] == "unconfirmed"
        )
        # This selection is request-scoped. FastMCP delivers the change on the
        # request's own stream, including when a modern listener is also open.
        async with asyncio.timeout(watchdog_timeout(10)):
            await received.wait()
        after = await names(client)
        assert "effect_probe" in after and "effect_probe" not in await names(other)
        for host_inventory, expected in ((initial, "host_filtered"), (after, "host_advertised")):
            compared = (
                await client.call_tool(
                    "manage_tools",
                    {
                        "action": "inspect_inventory",
                        "tool": "effect_probe",
                        "host_tool_names": sorted(host_inventory),
                    },
                )
            ).structured_content
            assert compared["status"] == expected
        call = await client.call_tool("effect_probe", {"strength": 2.5})
        assert call.structured_content == {"strength": 2.5, "enabled": True}
        if mode == "auto":
            routing["project"] = "Second@second"
            assert "effect_probe" not in await names(client)


@pytest.mark.asyncio
async def test_result_refresh_uses_the_request_editor_and_respects_its_toggles(
    catalog_server, monkeypatch
):
    server, registry, _, _ = catalog_server
    routing = next(item for item in server.middleware if isinstance(item, UnityInstanceMiddleware))
    # Restore the middleware's actual routing path for this two-Editor test.
    monkeypatch.delattr(routing, "_inject_unity_instance")
    monkeypatch.setattr(
        routing, "_maybe_autoselect_instance", AsyncMock(return_value="First@first")
    )
    await registry.register("one", "First", "first", "6000")
    await registry.register("two", "Second", "second", "6001")
    await publish(registry, "one", [])
    await publish(registry, "two", ["effect_probe"])
    async with run_server_async(server) as url, Client(url, mode="legacy", cache=False) as client:
        result = (
            await client.call_tool(
                "manage_tools", {"action": "activate", "group": "vfx", "unity_instance": "seco"}
            )
        ).structured_content
        request = result["refresh_request"]
        assert request == {
            "method": "tools/list",
            "params": {"_meta": {"unity_instance": "Second@second"}},
        }
        page = await client.session.list_tools(
            params=PaginatedRequestParams.model_validate(request["params"])
        )
        refreshed = {tool.name for tool in page.tools}
        assert "effect_probe" in refreshed and "texture_probe" not in refreshed
        assert "effect_probe" not in await names(client)
        await registry.unregister("two")
        with pytest.raises(MCPError):
            await client.session.list_tools(
                params=PaginatedRequestParams.model_validate(request["params"])
            )
        assert "manage_tools" in await names(client)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_prepare_compares_draft_selectors_on_the_current_request_router(
    catalog_server, monkeypatch, mode
):
    server, registry, _, _ = catalog_server
    routing = next(item for item in server.middleware if isinstance(item, UnityInstanceMiddleware))
    monkeypatch.delattr(routing, "_inject_unity_instance")
    monkeypatch.setattr(
        routing, "_maybe_autoselect_instance", AsyncMock(return_value="First@shared-amber")
    )
    await registry.register("one", "First", "shared-amber", "6000")
    await registry.register("two", "Second", "shared-blue", "6001")

    @server.tool
    def target_probe(unity_instance: str):
        raise AssertionError("prepare must not dispatch the target")

    async with run_server_async(server) as url, Client(url, mode=mode, cache=False) as client:

        async def prepare(outer, inner):
            return (
                await client.call_tool(
                    "manage_tools",
                    {
                        "action": "prepare",
                        "tool": "target_probe",
                        **({"unity_instance": outer} if outer is not None else {}),
                        "arguments": {"unity_instance": inner},
                    },
                )
            ).structured_content

        for outer, inner in (("shared-b", "shared-b"), ("shared-b", "SHARED-BL")):
            result = await prepare(outer, inner)
            assert result["prepared_call"]["arguments"] == {"unity_instance": "Second@shared-blue"}
        for inner in ("shared-a", "missing", "shared", None):
            assert not (await prepare("Second@shared-blue", inner))["ready"]

        # A draft may explicitly name the project whose catalog this request is reading.
        for inner in ("First@shared-amber", "shared-a"):
            result = await prepare(None, inner)
            assert result["prepared_call"]["arguments"] == {"unity_instance": "First@shared-amber"}
        for inner in ("Second@shared-blue", "missing", "shared", None):
            assert not (await prepare(None, inner))["ready"]
        await registry.unregister("one")
        assert not (await prepare(None, "First@shared-amber"))["ready"]
