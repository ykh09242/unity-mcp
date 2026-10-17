"""Authenticated catalog subscriptions use the real MCP HTTP transport."""

import json
from dataclasses import dataclass, field

import anyio
import httpx
import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.messages import MessageHandler
from fastmcp.client.transports import StreamableHttpTransport
from websockets.sync.client import connect
from mcp.client.subscriptions import listen
from mcp.shared.subscriptions import ToolsListChanged

from core.local_auth import LOCAL_AUTH_HEADER
from core.config import config
from tests.http.test_local_control_wire import local_server_process
from tests.http.test_mcp_protocol_wire import _body, _headers, _message
from transport.catalog_subscriptions import install_catalog_subscriptions


@dataclass(frozen=True)
class WireServer:
    url: str
    port: int
    token: str = field(repr=False)

    def __iter__(self):
        return iter((self.url, self.port, self.token))


@pytest.fixture(scope="module")
def catalog_wire(tmp_path_factory):
    with local_server_process(tmp_path_factory.mktemp("catalog-subscriptions")) as server:
        yield WireServer(*server)


def test_modern_discover_advertises_supported_resource_notifications(catalog_wire):
    # Given: the actual authenticated production server, with no initialize handshake.
    url, _, token = catalog_wire
    with httpx.Client(headers={LOCAL_AUTH_HEADER: token}, trust_env=False, timeout=5) as client:
        # When: a modern client discovers its capabilities.
        response = client.post(
            f"{url}/mcp",
            headers={
                **_headers("server/discover"),
                "Accept": "application/json, text/event-stream",
            },
            json=_body("server/discover"),
        )

    # Then: tools and fixed editor-state subscriptions are usable; list events stay declined.
    assert response.status_code == 200
    caps = response.json()["result"]["capabilities"]
    assert caps["tools"]["listChanged"] is True
    assert caps["prompts"].get("listChanged", False) is False
    assert caps["resources"].get("listChanged", False) is False
    assert caps["resources"]["subscribe"] is True


class LegacyChanges(MessageHandler):
    def __init__(self):
        self.changed = anyio.Event()

    async def on_tool_list_changed(self, message):
        self.changed.set()


async def next_message(lines):
    with anyio.fail_after(5):
        async for line in lines:
            if line.startswith("data: "):
                return json.loads(line[6:])
    pytest.fail("Subscription ended before the expected message")


@pytest.mark.asyncio
async def test_sdk_duplex_listen_releases_its_non_http_owner(monkeypatch):
    # Given: the same wrapper on the SDK's native duplex transport, without an HTTP owner.
    monkeypatch.setattr(config, "http_remote_hosted", False)
    server = FastMCP("catalog-duplex")
    runtime = install_catalog_subscriptions(server, remote_hosted=False)
    async with runtime.lifespan():
        async with Client(server) as client:
            async with listen(client.session, tools_list_changed=True) as subscription:
                runtime.publish()
                with anyio.fail_after(3):
                    event = await anext(aiter(subscription))
                assert isinstance(event, ToolsListChanged)
        assert not runtime._leases
        assert not runtime.bus.scopes


@pytest.mark.asyncio
async def test_actual_plugin_commits_notify_modern_and_explicit_legacy_clients(catalog_wire):
    # Given: authenticated native plugin and both protocol generations on one server.
    url, port, token = catalog_wire
    headers = {LOCAL_AUTH_HEADER: token}
    legacy_changes = LegacyChanges()
    async with Client(
        StreamableHttpTransport(f"{url}/mcp", headers=headers),
        mode="legacy",
        message_handler=legacy_changes,
    ) as legacy:
        assert any(tool.name == "set_active_instance" for tool in await legacy.list_tools())
        with connect(
            f"ws://127.0.0.1:{port}/hub/plugin", additional_headers=headers, proxy=None
        ) as plugin:
            assert json.loads(plugin.recv(timeout=5))["type"] == "welcome"
            plugin.send(
                json.dumps(
                    {
                        "type": "register",
                        "project_name": "SubscriptionWire",
                        "project_hash": "subscription-wire",
                        "unity_version": "6000.0",
                    }
                )
            )
            assert json.loads(plugin.recv(timeout=5))["type"] == "registered"
            async with httpx.AsyncClient(
                base_url=url,
                headers={**headers, "Accept": "application/json, text/event-stream"},
                trust_env=False,
                timeout=5,
            ) as http:
                async with http.stream(
                    "POST",
                    "/mcp",
                    headers=_headers("subscriptions/listen"),
                    json=_body(
                        "subscriptions/listen", {"notifications": {"toolsListChanged": True}}
                    ),
                ) as response:
                    assert response.status_code == 200
                    assert response.headers["content-type"].startswith("text/event-stream")
                    assert "mcp-session-id" not in response.headers
                    lines = response.aiter_lines()
                    ack = await next_message(lines)
                    assert ack["method"] == "notifications/subscriptions/acknowledged"
                    assert ack["params"]["notifications"] == {"toolsListChanged": True}
                    subscription_id = ack["params"]["_meta"][
                        "io.modelcontextprotocol/subscriptionId"
                    ]

                    # When: Unity publishes a custom schema, then replaces that schema.
                    for parameter in ("first_value", "replacement_value"):
                        legacy_changes.changed = anyio.Event()
                        plugin.send(
                            json.dumps(
                                {
                                    "type": "register_tools",
                                    "tools": [
                                        {
                                            "name": "subscription_wire_tool",
                                            "parameters": [{"name": parameter, "type": "string"}],
                                        }
                                    ],
                                }
                            )
                        )
                        changed = await next_message(lines)
                        assert changed["method"] == "notifications/tools/list_changed"
                        assert (
                            changed["params"]["_meta"]["io.modelcontextprotocol/subscriptionId"]
                            == subscription_id
                        )
                        listing = await http.post(
                            "/mcp", headers=_headers("tools/list"), json=_body("tools/list")
                        )
                        tool = next(
                            tool
                            for tool in _message(listing)["result"]["tools"]
                            if tool["name"] == "subscription_wire_tool"
                        )
                        assert list(tool["inputSchema"]["properties"]) == [parameter]
                        with anyio.fail_after(5):
                            await legacy_changes.changed.wait()
                    # Then: legacy invalidation remains usable, and non-listening clients can refetch.
                    with anyio.fail_after(5):
                        await legacy_changes.changed.wait()
                    assert any(
                        tool.name == "subscription_wire_tool" for tool in await legacy.list_tools()
                    )
                    plugin.close()
                    removed = await next_message(lines)
                    assert removed["method"] == "notifications/tools/list_changed"
                    listing = await http.post(
                        "/mcp", headers=_headers("tools/list"), json=_body("tools/list")
                    )
                    assert all(
                        tool["name"] != "subscription_wire_tool"
                        for tool in _message(listing)["result"]["tools"]
                    )
