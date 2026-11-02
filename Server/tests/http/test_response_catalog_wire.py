"""Oversized inventories fail explicitly; oversized prompts remain recoverable."""

import json
import logging

import httpx
import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError, ResourceError, PromptError, ValidationError

from services.tools.read_response import read_response
from tests.http.test_mcp_protocol_wire import _body, _headers, _message
from transport.response_delivery import MAX_MCP_RESPONSE_BYTES
from transport.response_limit_middleware import ResponseLimitMiddleware
from models.response_limits import response_owner


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["tool", "resource", "template", "prompt", "prompt_body", "discover"]
)
async def test_oversized_known_catalog_and_prompt_paths_fit_actual_http(kind):
    text = "x" * MAX_MCP_RESPONSE_BYTES
    server = FastMCP("catalog-delivery", instructions=text if kind == "discover" else None)
    server.add_middleware(ResponseLimitMiddleware())
    description = "bounded" if kind == "prompt_body" else text

    def simple() -> str:
        return "small"

    def template(name: str) -> str:
        return name

    def prompt() -> str:
        return text if kind == "prompt_body" else "small"

    match kind:
        case "tool" | "discover":
            server.tool(simple, description=description)
            method, params = ("server/discover" if kind == "discover" else "tools/list"), {}
        case "resource":
            server.resource("fixture://catalog", description=description)(simple)
            method, params = "resources/list", {}
        case "template":
            server.resource("fixture://catalog/{name}", description=description)(template)
            method, params = "resources/templates/list", {}
        case "prompt" | "prompt_body":
            server.prompt(prompt, description=description)
            method, params = (
                ("prompts/get", {"name": "prompt"})
                if kind == "prompt_body"
                else ("prompts/list", {})
            )
        case _:
            raise AssertionError(kind)
    server.tool(read_response)
    app = server.http_app(path="/mcp", stateless_http=True, json_response=True)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            response = await client.post(
                "/mcp",
                headers=_headers(method, name=params.get("name")),
                json=_body(method, params),
            )
            assert len(response.content) < MAX_MCP_RESPONSE_BYTES
            wire = _message(response)
            if kind == "prompt_body":
                handle = json.loads(wire["result"]["messages"][0]["content"]["text"])["delivery"]
            else:
                assert wire["error"]["code"] == -32000
                handle = wire["error"]["data"]["delivery"]
            assert handle["state"] == "retained"
            response = await client.post(
                "/mcp",
                headers=_headers("tools/call", name="read_response"),
                json=_body(
                    "tools/call",
                    {"name": "read_response", "arguments": {"response_id": handle["response_id"]}},
                ),
            )
            assert len(response.content) < MAX_MCP_RESPONSE_BYTES
            assert _message(response)["result"]["structuredContent"]["success"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["tool", "resource", "prompt"])
@pytest.mark.parametrize(
    "large,error_kind",
    [
        (False, "visible"),
        (True, "visible"),
        (False, "validation"),
        (True, "validation"),
        (False, "unexpected"),
    ],
)
async def test_expected_sdk_error_paths_preserve_errors_with_bounded_wire(kind, large, error_kind):
    server = FastMCP("visible-error-delivery", mask_error_details=True)
    server.add_middleware(ResponseLimitMiddleware())
    text = "known-error" if not large else "x" * (MAX_MCP_RESPONSE_BYTES + 1)
    calls = []
    retained = {}

    def failure():
        if error_kind == "unexpected":
            return RuntimeError("hidden-implementation-detail")
        error_type = (
            ValidationError
            if error_kind == "validation"
            else {"tool": ToolError, "resource": ResourceError, "prompt": PromptError}[kind]
        )
        return error_type(text, log_level=logging.DEBUG)

    def fail_tool() -> str:
        calls.append(1)
        raise failure()

    def fail_resource() -> str:
        calls.append(1)
        if large:
            retained["native-result"] = 1
            response_owner.get().entries.append((retained, "native-result"))
        raise failure()

    def fail_prompt() -> str:
        calls.append(1)
        if large:
            retained["native-result"] = 1
            response_owner.get().entries.append((retained, "native-result"))
        raise failure()

    match kind:
        case "tool":
            server.tool(fail_tool)
            method, params, name = "tools/call", {"name": "fail_tool", "arguments": {}}, "fail_tool"
        case "resource":
            server.resource("fixture://error")(fail_resource)
            method, params, name = "resources/read", {"uri": "fixture://error"}, "fixture://error"
        case "prompt":
            server.prompt(fail_prompt)
            method, params, name = "prompts/get", {"name": "fail_prompt"}, "fail_prompt"
        case _:
            raise AssertionError(kind)
    server.tool(read_response)
    app = server.http_app(path="/mcp", stateless_http=True, json_response=True)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            response = await client.post(
                "/mcp", headers=_headers(method, name=name), json=_body(method, params)
            )
            assert len(response.content) < MAX_MCP_RESPONSE_BYTES
            wire = _message(response)
            if error_kind == "unexpected" and kind != "prompt":
                assert "hidden-implementation-detail" not in response.text
            if error_kind == "unexpected" and kind == "prompt":
                # The pinned SDK wraps unexpected prompt exceptions before its
                # masking boundary. Preserve that existing behavior exactly.
                baseline = FastMCP("prompt-error-oracle", mask_error_details=True)
                baseline.prompt(fail_prompt)
                baseline_app = baseline.http_app(
                    path="/mcp", stateless_http=True, json_response=True
                )
                async with baseline_app.router.lifespan_context(baseline_app):
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=baseline_app), base_url="http://fixture"
                    ) as oracle:
                        observed = await oracle.post(
                            "/mcp", headers=_headers(method, name=name), json=_body(method, params)
                        )
                assert wire["error"] == _message(observed)["error"]
            if kind == "tool":
                assert wire["result"]["isError"] is True
                handle = wire["result"].get("structuredContent", {}).get("delivery")
                if not large and error_kind != "unexpected":
                    assert wire["result"]["content"][0]["text"] == text
            else:
                assert wire["error"]["code"] == (-32602 if error_kind == "validation" else -32603)
                handle = (wire["error"].get("data") or {}).get("delivery")
                if not large and error_kind != "unexpected":
                    assert wire["error"]["message"] == text
            if large:
                assert handle["state"] == "retained"
                response = await client.post(
                    "/mcp",
                    headers=_headers("tools/call", name="read_response"),
                    json=_body(
                        "tools/call",
                        {
                            "name": "read_response",
                            "arguments": {"response_id": handle["response_id"]},
                        },
                    ),
                )
                assert len(response.content) < MAX_MCP_RESPONSE_BYTES
                assert _message(response)["result"]["structuredContent"]["success"] is True
    assert calls == ([1, 1] if error_kind == "unexpected" and kind == "prompt" else [1])
    assert retained == {}
