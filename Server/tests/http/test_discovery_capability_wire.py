"""Selection support is visible before a production HTTP client tries activation."""

import json

import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from core.local_auth import LOCAL_AUTH_HEADER
from tests.http.test_mcp_protocol_wire import (
    _body,
    _headers,
    _message,
    protocol_http,  # noqa: F401
    protocol_server,  # noqa: F401
)


def test_modern_http_preflight_exposes_static_selection_and_startup_guidance(protocol_http):
    discovered = _message(
        protocol_http.post(
            "/mcp", headers=_headers("server/discover"), json=_body("server/discover")
        )
    )["result"]
    assert 'manage_tools(action="list_groups").discovery' in discovered["instructions"]
    assert "supported client scope/project binding" in discovered["instructions"]
    assert "UNITY_MCP_TOOL_PROFILE=full" in discovered["instructions"]
    params = {"name": "manage_tools", "arguments": {"action": "list_groups"}}
    response = protocol_http.post(
        "/mcp",
        headers={**_headers("tools/call", name="manage_tools"), "X-API-Key": "untrusted-label"},
        json=_body("tools/call", params),
    )
    capability = _message(response)["result"]["structuredContent"]["discovery"]
    assert capability["profile"] == "core"
    assert capability["mode"] == "static_profile"
    assert capability["reason"] == "verified_sdk_principal_required"
    assert capability["selection_supported"] is False
    assert "no supported stable client scope and resolved project" in capability["guidance"]
    assert "UNITY_MCP_TOOL_PROFILE=full" in capability["guidance"]

    resource = {"uri": "mcpforunity://doctor/runtime"}
    response = protocol_http.post(
        "/mcp",
        headers=_headers("resources/read", name=resource["uri"]),
        json=_body("resources/read", resource),
    )
    report = json.loads(_message(response)["result"]["contents"][0]["text"])
    assert report["discovery"] == capability


@pytest.mark.asyncio
async def test_legacy_http_preflight_exposes_connection_selection(protocol_http):
    transport = StreamableHttpTransport(
        str(protocol_http.base_url.join("mcp")),
        headers={LOCAL_AUTH_HEADER: protocol_http.headers[LOCAL_AUTH_HEADER]},
    )
    async with Client(transport, mode="legacy", cache=False) as client:
        groups = (
            await client.call_tool("manage_tools", {"action": "list_groups"})
        ).structured_content
        assert groups["discovery"]["mode"] == "legacy_connection"
        assert groups["discovery"]["selection_supported"] is True
        activated = (
            await client.call_tool("manage_tools", {"action": "activate", "group": "vfx"})
        ).structured_content
        assert activated["activated"] == "vfx"
