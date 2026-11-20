"""Two readers retrieve the same retained scenario evidence over the actual MCP HTTP app."""

import asyncio
import base64
from hashlib import sha256
from io import BytesIO
import json

import httpx
import pytest
from fastmcp import FastMCP
from fastmcp.tools import ToolResult
from mcp_types import ImageContent, TextContent
from PIL import Image

from models.response_limits import response_owner
from services.response_delivery import RETENTION_SECONDS, response_store
from services.tools.read_response import read_response
from tests.http.test_mcp_protocol_wire import _body, _headers, _message
from tests.timing import watchdog_timeout
from transport.response_limit_middleware import ResponseLimitMiddleware, ResponseRetentionMiddleware
from utils.retained_response import assemble_response


@pytest.mark.asyncio
async def test_cancelled_report_reader_releases_send_and_other_reader_verifies_png(monkeypatch):
    # A valid, intentionally uncompressed PNG makes the SDK response exceed the inline limit.
    with Image.new("RGB", (1536, 1536), "blue") as pixels, BytesIO() as encoded:
        pixels.save(encoded, format="PNG", compress_level=0)
        png = encoded.getvalue()
    report = json.dumps({"checkpoint": "ready", "sha256": sha256(png).hexdigest()})
    server = FastMCP("scenario-artifacts")
    server.add_middleware(ResponseLimitMiddleware())
    calls = 0

    @server.tool
    def evidence() -> ToolResult:
        nonlocal calls
        calls += 1
        return ToolResult(
            content=[
                TextContent(text=report),
                ImageContent(
                    type="image", mimeType="image/png", data=base64.b64encode(png).decode()
                ),
            ]
        )

    server.tool(read_response)
    now = 100.0
    store = response_store(server)
    monkeypatch.setattr(store, "_clock", lambda: now)
    sdk = server.http_app(path="/mcp", stateless_http=True, json_response=True)
    transport = ResponseRetentionMiddleware(sdk)
    blocked = asyncio.Event()
    owner = None

    async def app(scope, receive, send):
        async def delayed_send(message):
            nonlocal owner
            if b"x-delayed-reader" in dict(scope["headers"]) and message.get("body"):
                owner = response_owner.get()
                blocked.set()
                await asyncio.Event().wait()
            await send(message)

        await transport(scope, receive, delayed_send)

    async with (
        sdk.router.lifespan_context(sdk),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://fixture"
        ) as client,
    ):

        async def call(name, arguments, *, delayed=False):
            headers = dict(_headers("tools/call", name=name))
            if delayed:
                headers["x-delayed-reader"] = "1"
            reply = await client.post(
                "/mcp",
                headers=headers,
                json=_body("tools/call", {"name": name, "arguments": arguments}),
            )
            reply.raise_for_status()
            return _message(reply)["result"]["structuredContent"]

        handle = (await call("evidence", {}))["delivery"]
        assert handle["state"] == "retained"

        async def slow(name, arguments):
            return await call(name, arguments, delayed=arguments["offset"] > 0)

        limits = {"max_bytes": handle["total_bytes"], "max_pages": 64, "timeout_seconds": 120}
        task = asyncio.create_task(assemble_response(slow, handle, **limits))
        try:
            await asyncio.wait_for(blocked.wait(), watchdog_timeout())
            assert owner is not None and not owner.released
            value = await assemble_response(call, handle, **limits)
            received_report = json.loads(value["content"][0]["text"])
            received_png = base64.b64decode(value["content"][1]["data"], validate=True)
            assert received_report == json.loads(report)
            assert sha256(received_png).hexdigest() == received_report["sha256"]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled() and owner.released
        assert calls == 1 and store.retained_bytes == handle["total_bytes"]

        async def truncated(name, arguments):
            result = await call(name, arguments)
            result["data"]["data_base64"] = result["data"]["data_base64"][:-4]
            return result

        with pytest.raises(ValueError, match="response_page_sequence_mismatch"):
            await assemble_response(truncated, handle, **limits)
        now += RETENTION_SECONDS
        with pytest.raises(ValueError, match="response_page_unavailable"):
            await assemble_response(call, handle, **limits)
        assert store.retained_bytes == 0 and calls == 1
