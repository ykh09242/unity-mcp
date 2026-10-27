"""The reviewed HTTP dependency set preserves bounded streaming delivery."""

import asyncio
import json
from types import SimpleNamespace
import socket

import httpx2
import pytest
import pytest_asyncio
import uvicorn
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from mcp.shared.exceptions import MCPError

from core.config import config
from core.local_auth import LOCAL_AUTH_HEADER
from models.response_limits import MAX_RESPONSE_BYTES
from tests.http.test_plugin_command_progress import _ReadyServer
from tests.timing import await_signal, watchdog_timeout
from transport.response_limit_middleware import ResponseLimitMiddleware


@pytest_asyncio.fixture
async def streaming_http(monkeypatch):
    """Own a loopback listener with product middleware and one synthetic tool."""
    from main import UnityMCP

    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "local_auth_token", "owned-dependency-streaming-token")
    server = UnityMCP("dependency-streaming")
    server.add_middleware(ResponseLimitMiddleware())
    payload = "x" * (600 * 1024)

    @server.tool
    async def large_result() -> dict:
        return {"success": True, "data": {"preview": payload}}

    app = server.http_app(path="/mcp", stateless_http=True, json_response=False)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(16)
        runner = _ReadyServer(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=listener.getsockname()[1],
                access_log=False,
                log_level="error",
                timeout_graceful_shutdown=2,
                ws="none",
            )
        )
        task = asyncio.create_task(runner.serve(sockets=[listener]))
        try:
            await await_signal(runner.ready, "dependency HTTP listener startup")
            assert runner.started and not task.done()
            yield SimpleNamespace(
                url=f"http://127.0.0.1:{listener.getsockname()[1]}/mcp",
                payload=payload,
                headers={LOCAL_AUTH_HEADER: config.local_auth_token},
            )
        finally:
            runner.should_exit = True
            try:
                await asyncio.wait_for(task, watchdog_timeout())
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_large_http_result_uses_explicit_finite_application_limit(streaming_http, mode):
    # Given: text plus structured content exceeds the SDK's default 1 MiB event limit.
    transport = StreamableHttpTransport(
        streaming_http.url,
        headers=streaming_http.headers,
        max_sse_event_size=MAX_RESPONSE_BYTES,
    )
    async with Client(transport, mode=mode, timeout=watchdog_timeout()) as client:
        # When: a supported bounded result crosses the real streaming HTTP boundary.
        result = await client.call_tool("large_result")

        # Then: both representations arrive intact, and the same client remains usable.
        assert result.structured_content == {
            "success": True,
            "data": {"preview": streaming_http.payload},
        }
        assert json.loads(result.content[0].text) == result.structured_content
        assert len(result.content[0].text.encode()) + len(streaming_http.payload) > 1024 * 1024
        assert [tool.name for tool in await client.list_tools()] == ["large_result"]


@pytest.mark.asyncio
async def test_default_sse_limit_rejects_oversized_result(streaming_http):
    # Given: the ordinary client retains the SDK's finite 1 MiB default limit.
    transport = StreamableHttpTransport(streaming_http.url, headers=streaming_http.headers)
    async with Client(transport, mode="legacy", timeout=watchdog_timeout()) as client:
        # When: its legacy SSE response exceeds that default.
        with pytest.raises(MCPError, match="exceeded the 1048576 byte limit"):
            await client.call_tool("large_result")

        # Then: rejection leaves the transport usable for ordinary smaller responses.
        assert [tool.name for tool in await client.list_tools()] == ["large_result"]


@pytest.mark.asyncio
async def test_head_does_not_open_an_mcp_event_stream(streaming_http):
    # Given: the product MCP route supports GET event streams but not HEAD operations.
    async with httpx2.AsyncClient(trust_env=False, timeout=watchdog_timeout()) as client:
        # When: a client sends HEAD on an otherwise reusable connection.
        response = await client.head(streaming_http.url, headers=streaming_http.headers)

        # Then: the unsupported operation ends without a body or a lingering stream.
        assert response.status_code == 405
        assert response.content == b""
        following = await client.head(streaming_http.url, headers=streaming_http.headers)
        assert following.status_code == 405
