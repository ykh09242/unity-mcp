"""Pin modern and explicit legacy MCP contracts at the authenticated HTTP boundary."""

import base64
import json
from collections.abc import Iterator, Mapping
from typing import Final

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from pydantic import JsonValue

from core.local_auth import LOCAL_AUTH_HEADER
from tests.http.test_local_control_wire import running_server  # pylint: disable=unused-import

MODERN_VERSION: Final = "2026-07-28"
LEGACY_VERSION: Final = "2025-11-25"
VERSION_META: Final = "io.modelcontextprotocol/protocolVersion"
CAPABILITIES_META: Final = "io.modelcontextprotocol/clientCapabilities"
CLIENT_INFO_META: Final = "io.modelcontextprotocol/clientInfo"


@pytest.fixture
def protocol_http(running_server) -> Iterator[httpx.Client]:
    """Reuse the production subprocess without exposing its token in test parameters."""
    url, _, token = running_server
    with httpx.Client(
        base_url=url,
        headers={LOCAL_AUTH_HEADER: token, "Accept": "application/json, text/event-stream"},
        timeout=10,
        trust_env=False,
    ) as client:
        yield client


def _body(
    method: str,
    params: Mapping[str, JsonValue] | None = None,
    *,
    version: str = MODERN_VERSION,
    omit_meta: tuple[str, ...] = (),
) -> Mapping[str, JsonValue]:
    """Build a raw envelope; response parsing deliberately bypasses SDK defaults."""
    meta = {
        VERSION_META: version,
        CAPABILITIES_META: {},
        CLIENT_INFO_META: {"name": "protocol-wire-test", "version": "1.0"},
    }
    for key in omit_meta:
        meta.pop(key)
    return {
        "jsonrpc": "2.0",
        "id": "wire-request",
        "method": method,
        "params": {**(params or {}), "_meta": meta},
    }


def _headers(method: str, *, name: str | None = None) -> Mapping[str, str]:
    headers = {"MCP-Protocol-Version": MODERN_VERSION, "Mcp-Method": method}
    if name is not None:
        headers["Mcp-Name"] = name
    return headers


def _message(response: httpx.Response) -> Mapping[str, JsonValue]:
    """Read JSON or a finite SSE reply, as both encodings are permitted on the wire."""
    match response.headers["content-type"].split(";", 1)[0]:
        case "application/json":
            return response.json()
        case "text/event-stream":
            messages = [
                json.loads(line.removeprefix("data: "))
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            return next(message for message in messages if message.get("id") == "wire-request")
        case unexpected:
            pytest.fail(f"Unexpected MCP response content type: {unexpected}")


@pytest.mark.parametrize("omit_client_info", [False, True])
def test_modern_discovery_is_self_contained(protocol_http, omit_client_info):
    # Given: no initialize exchange, with client identity supplied or legitimately omitted.
    request = _body("server/discover", omit_meta=(CLIENT_INFO_META,) if omit_client_info else ())

    # When: the exact modern version is sent directly to the production MCP endpoint.
    response = protocol_http.post("/mcp", headers=_headers("server/discover"), json=request)

    # Then: discovery supplies version, capabilities, identity and complete cache metadata.
    assert response.status_code == 200
    message = _message(response)
    assert message["jsonrpc"] == "2.0"
    assert message["id"] == "wire-request"
    result = message["result"]
    assert result["resultType"] == "complete"
    assert MODERN_VERSION in result["supportedVersions"]
    assert "tools" in result["capabilities"]
    info = result["_meta"]["io.modelcontextprotocol/serverInfo"]
    assert info["name"] == "mcp-for-unity-server"
    assert info["version"]
    assert type(result["ttlMs"]) is int and result["ttlMs"] >= 0
    assert result["cacheScope"] in {"public", "private"}
    assert "mcp-session-id" not in response.headers


@pytest.mark.parametrize(
    ("method", "params", "collection"),
    [
        ("tools/list", {}, "tools"),
        ("prompts/list", {}, "prompts"),
        ("resources/list", {}, "resources"),
        ("resources/templates/list", {}, "resourceTemplates"),
        ("resources/read", {"uri": "mcpforunity://instances"}, "contents"),
    ],
)
def test_modern_cacheable_operations_carry_required_hints(
    protocol_http, method, params, collection
):
    # Given: each cacheable operation, with an explicit URI for the local registry read.
    headers = _headers(method, name=params.get("uri"))

    # When: a standalone modern request reaches the registered production operation.
    response = protocol_http.post("/mcp", headers=headers, json=_body(method, params))

    # Then: the raw result includes mandatory fields; no decoder inserts defaults.
    assert response.status_code == 200
    result = _message(response)["result"]
    assert result["resultType"] == "complete"
    assert type(result["ttlMs"]) is int and result["ttlMs"] >= 0
    assert result["cacheScope"] in {"public", "private"}
    assert isinstance(result[collection], list)
    assert "mcp-session-id" not in response.headers
    if method == "tools/list":
        assert any(tool["name"] == "set_active_instance" for tool in result["tools"])
    if method == "resources/read":
        content = result["contents"][0]
        assert content["uri"] == "mcpforunity://instances"
        assert json.loads(content["text"])["instance_count"] == 0


@pytest.mark.parametrize("session_header", [None, "unrelated-legacy-session"])
def test_modern_tool_result_does_not_mint_or_echo_protocol_session(protocol_http, session_header):
    # Given: a real local-only tool and an optional obsolete protocol session header.
    headers = dict(_headers("tools/call", name="set_active_instance"))
    if session_header is not None:
        headers["Mcp-Session-Id"] = session_header

    # When: a call that cannot select implicit state runs without any initialize handshake.
    response = protocol_http.post(
        "/mcp",
        headers=headers,
        json=_body(
            "tools/call", {"name": "set_active_instance", "arguments": {"instance": "unused"}}
        ),
    )

    # Then: the operation completes with its domain outcome and no protocol session.
    assert response.status_code == 200
    result = _message(response)["result"]
    assert result["resultType"] == "complete"
    assert result["structuredContent"]["success"] is False
    assert "mcp-session-id" not in response.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [MODERN_VERSION, "legacy"])
async def test_explicit_sdk_modes_use_the_real_authenticated_endpoint(protocol_http, mode):
    # Given: a pinned modern client or an explicitly legacy client, with fallback disabled.
    transport = StreamableHttpTransport(
        str(protocol_http.base_url.join("mcp")),
        headers={LOCAL_AUTH_HEADER: protocol_http.headers[LOCAL_AUTH_HEADER]},
    )
    async with Client(transport, mode=mode, timeout=10, init_timeout=10) as client:
        # When: the SDK requests the production tool catalog over real HTTP.
        tools = await client.list_tools()

        # Then: the selected era and its initialize semantics remain explicit.
        assert any(tool.name == "set_active_instance" for tool in tools)
        assert client.protocol_version == (LEGACY_VERSION if mode == "legacy" else MODERN_VERSION)
        assert (client.initialize_result is None) is (mode == MODERN_VERSION)


@pytest.mark.parametrize("header_value", [None, "tools/list", "Server/Discover"])
def test_modern_missing_or_mismatched_method_header_is_rejected(protocol_http, header_value):
    # Given: a valid body whose required method mirror is missing or disagrees.
    headers = dict(_headers("server/discover"))
    if header_value is None:
        headers.pop("Mcp-Method")
    else:
        headers["Mcp-Method"] = header_value

    # When: the inconsistent request crosses the production transport boundary.
    response = protocol_http.post("/mcp", headers=headers, json=_body("server/discover"))

    # Then: HTTP and JSON-RPC both report the normative header mismatch.
    assert response.status_code == 400
    message = _message(response)
    assert message["id"] == "wire-request"
    assert message["error"]["code"] == -32020
    assert "result" not in message


@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("tools/call", {"name": "set_active_instance", "arguments": {"instance": "unused"}}),
        ("resources/read", {"uri": "mcpforunity://instances"}),
        ("prompts/get", {"name": "wire-unknown-prompt"}),
    ],
)
@pytest.mark.parametrize("header_value", [None, "wrong-name", "=?base64?not-base64?="])
def test_modern_name_bearing_operations_validate_header(
    protocol_http, method, params, header_value
):
    # Given: a name-bearing operation whose mirror is absent, different or malformed.
    headers = _headers(method, name=header_value)

    # When: the body and headers are submitted together.
    response = protocol_http.post("/mcp", headers=headers, json=_body(method, params))

    # Then: header validation rejects before any tool, resource or prompt dispatch.
    assert response.status_code == 400
    message = _message(response)
    assert message["id"] == "wire-request"
    assert message["error"]["code"] == -32020


def test_modern_name_header_accepts_decoded_base64(protocol_http):
    # Given: the correct resource URI encoded with the standard header sentinel.
    uri = "mcpforunity://instances"
    encoded = base64.b64encode(uri.encode("utf-8")).decode("ascii")

    # When: the recognized name mirror is compared to the resource URI in the body.
    response = protocol_http.post(
        "/mcp",
        headers=_headers("resources/read", name=f"=?base64?{encoded}?="),
        json=_body("resources/read", {"uri": uri}),
    )

    # Then: decoding allows the real resource to be read successfully.
    assert response.status_code == 200
    assert _message(response)["result"]["contents"][0]["uri"] == uri


@pytest.mark.parametrize(
    ("header_version", "body_version"),
    [(MODERN_VERSION, LEGACY_VERSION), (LEGACY_VERSION, MODERN_VERSION)],
)
def test_modern_header_and_body_protocol_versions_must_agree(
    protocol_http, header_version, body_version
):
    # Given: a modern envelope or HTTP version disagrees with its mirrored version.
    request = _body("server/discover", version=body_version)
    headers = {**_headers("server/discover"), "MCP-Protocol-Version": header_version}

    # When: the transport compares the mirrored protocol version to the body.
    response = protocol_http.post("/mcp", headers=headers, json=request)

    # Then: a mismatch is distinct from unsupported-version negotiation.
    assert response.status_code == 400
    message = _message(response)
    assert message["jsonrpc"] == "2.0"
    assert message["id"] == "wire-request"
    assert message["error"]["code"] == -32020
    assert "result" not in message


@pytest.mark.parametrize("missing_key", [VERSION_META, CAPABILITIES_META])
def test_modern_required_request_metadata_is_validated(protocol_http, missing_key):
    # Given: one required per-request metadata field is absent.
    request = _body("server/discover", omit_meta=(missing_key,))

    # When: the modern transport validates the envelope.
    response = protocol_http.post("/mcp", headers=_headers("server/discover"), json=request)

    # Then: missing body metadata is a parameter error, not a negotiation error.
    assert response.status_code == 400
    assert _message(response)["error"]["code"] == -32602


def test_unsupported_modern_version_advertises_supported_versions(protocol_http):
    # Given: an unsupported version mirrored consistently in header and body.
    version = "2099-01-01"
    headers = {**_headers("server/discover"), "MCP-Protocol-Version": version}

    # When: a client probes the production endpoint with that version.
    response = protocol_http.post(
        "/mcp", headers=headers, json=_body("server/discover", version=version)
    )

    # Then: the version negotiation error includes usable supported/requested values.
    assert response.status_code == 400
    error = _message(response)["error"]
    assert error["code"] == -32022
    assert error["data"]["requested"] == version
    assert MODERN_VERSION in error["data"]["supported"]


def test_modern_unknown_method_returns_structured_not_found(protocol_http):
    # Given: a coherent modern request for an unimplemented RPC method.
    method = "wire/unknown"

    # When: it reaches dispatch through the production endpoint.
    response = protocol_http.post("/mcp", headers=_headers(method), json=_body(method))

    # Then: modern error recognition can distinguish it from a legacy endpoint miss.
    assert response.status_code == 404
    assert _message(response)["error"]["code"] == -32601


@pytest.mark.parametrize("browser_origin", [None, "https://attacker.example"])
def test_modern_protocol_validation_stays_behind_local_authentication(
    protocol_http, browser_origin
):
    # Given: an inconsistent modern request from an unauthenticated or browser caller.
    request = protocol_http.build_request(
        "POST", "/mcp", headers=_headers("tools/list"), json=_body("server/discover")
    )
    if browser_origin is None:
        del request.headers[LOCAL_AUTH_HEADER]
    else:
        request.headers["Origin"] = browser_origin

    # When: it reaches the same real endpoint as conforming native requests.
    response = protocol_http.send(request)

    # Then: authentication/provenance rejects before the protocol validator runs.
    assert response.status_code == (401 if browser_origin is None else 403)
    assert response.json()["success"] is False
    assert "jsonrpc" not in response.json()


def test_explicit_legacy_initialize_and_tool_listing_remain_compatible(protocol_http):
    # Given: an explicit pre-modern initialize offer without modern mirrored headers.
    initialized = protocol_http.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": "wire-request",
            "method": "initialize",
            "params": {
                "protocolVersion": LEGACY_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "legacy-wire-test", "version": "1.0"},
            },
        },
    )
    assert initialized.status_code == 200
    result = _message(initialized)["result"]
    assert result["protocolVersion"] == LEGACY_VERSION
    assert result["serverInfo"]["name"] == "mcp-for-unity-server"
    assert "resultType" not in result
    headers = {"MCP-Protocol-Version": LEGACY_VERSION}
    if session_id := initialized.headers.get("mcp-session-id"):
        headers["Mcp-Session-Id"] = session_id
    ready = protocol_http.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert ready.status_code == 202
    assert ready.content == b""

    # When: a subsequent legacy operation carries the negotiated version/session.
    response = protocol_http.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": "wire-request", "method": "tools/list", "params": {}},
    )

    # Then: legacy payloads retain their established shape and real tool catalog.
    assert response.status_code == 200
    result = _message(response)["result"]
    assert any(tool["name"] == "set_active_instance" for tool in result["tools"])
    assert "resultType" not in result
    assert "ttlMs" not in result
    assert "cacheScope" not in result
