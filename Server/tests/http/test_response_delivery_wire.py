"""Measure real HTTP MCP response bytes at the client transport boundary."""

import base64
import json
import time

import httpx
import pytest
from fastmcp import FastMCP
from fastmcp.tools import ToolResult
from fastmcp.server.middleware import Middleware
from mcp_types import ImageContent, TextContent

from services.tools.read_response import read_response
from tests.http.test_mcp_protocol_wire import _body, _headers, _message
from transport.response_delivery import MAX_MCP_RESPONSE_BYTES, MCP_RESULT_BYTES
from transport.response_limit_middleware import ResponseLimitMiddleware
from transport.test_wait_binding import install_test_job_tasks, _SdkResultKernel
from transport.test_job_tasks import WaitTaskDeliveryMiddleware
from transport.local_auth_middleware import AUTHENTICATED_LOCAL_STATE
from services.test_wait_journal import DeadlineCheckpoint, WaitOperation


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["near", "over", "unicode", "base64", "resource"])
async def test_actual_wire_and_retrieval_pages_fit_codex_ceiling(kind):
    server = FastMCP("wire-delivery")
    server.add_middleware(ResponseLimitMiddleware())
    text = (
        "한" * (3 * 1024 * 1024)
        if kind == "unicode"
        else "x" * (MCP_RESULT_BYTES - 512 if kind == "near" else MAX_MCP_RESPONSE_BYTES + 1)
    )
    executions = 0

    @server.tool
    def pressure() -> ToolResult:
        nonlocal executions
        executions += 1
        if kind == "base64":
            content = ImageContent(
                type="image",
                data=base64.b64encode(b"x" * (6 * 1024 * 1024)).decode("ascii"),
                mimeType="image/png",
            )
        else:
            content = TextContent(text=text)
        return ToolResult(content=[content])

    @server.resource("fixture://large")
    def large_resource() -> str:
        return text

    server.tool(read_response)
    app = server.http_app(path="/mcp", stateless_http=True, json_response=True)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://fixture"
        ) as client:
            method = "resources/read" if kind == "resource" else "tools/call"
            name = "fixture://large" if kind == "resource" else "pressure"
            params = {"uri": name} if kind == "resource" else {"name": name, "arguments": {}}
            response = await client.post(
                "/mcp", headers=_headers(method, name=name), json=_body(method, params)
            )
            assert response.status_code == 200
            assert len(response.content) < MAX_MCP_RESPONSE_BYTES
            result = _message(response)["result"]
            if kind == "near":
                assert result["content"][0]["text"] == text
                return
            summary = (
                json.loads(result["contents"][0]["text"])
                if kind == "resource"
                else result["structuredContent"]
            )
            handle = summary["delivery"]
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
            page = _message(response)["result"]["structuredContent"]["data"]
            assert len(base64.b64decode(page["data_base64"])) == 256 * 1024
    assert executions == (0 if kind == "resource" else 1)


@pytest.mark.asyncio
async def test_task_journal_keeps_full_result_while_tasks_get_delivers_bounded_handle(tmp_path):
    server = FastMCP("task-delivery")
    project = "0123456789abcdef"

    class ResolvedProject(Middleware):
        async def on_call_tool(self, context, call_next):
            await context.fastmcp_context.set_state(
                "unity_instance", "Fixture@" + project, serializable=False
            )
            return await call_next(context)

    server.add_middleware(ResponseLimitMiddleware())
    server.add_middleware(ResolvedProject())
    server.tool(read_response)
    runtime = install_test_job_tasks(server, tmp_path / "tasks", remote_hosted=False)
    assert runtime is not None
    text = "x" * (MAX_MCP_RESPONSE_BYTES + 1)
    original = _SdkResultKernel(server).serialize(ToolResult(content=[TextContent(text=text)]))
    assert original["content"][0]["text"] == text
    sdk = server.http_app(path="/mcp", stateless_http=True, json_response=True)
    delivery_app = WaitTaskDeliveryMiddleware(sdk)

    async def authenticated(scope, receive, send):
        owned = {**scope, "state": {**scope.get("state", {}), AUTHENTICATED_LOCAL_STATE: True}}
        await delivery_app(owned, receive, send)

    async with sdk.router.lifespan_context(sdk):
        now = time.time()
        record = runtime.journal.create(
            WaitOperation(
                owner=runtime.journal.local_owner,
                project=project,
                instance="Fixture@" + project,
                session_id="native-generation",
                job_id="existing-job",
                wait=DeadlineCheckpoint.capture(10, now=now),
                outer=DeadlineCheckpoint.capture(30, now=now),
            )
        )
        runtime.journal.finish(record.task_id, "completed", result=original)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=authenticated), base_url="http://fixture"
        ) as client:
            body = _body("tasks/get", {"taskId": record.task_id})
            body["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"] = {
                "extensions": {"io.modelcontextprotocol/tasks": {}}
            }
            handles = []
            for _ in range(2):
                response = await client.post(
                    "/mcp", headers=_headers("tasks/get", name=record.task_id), json=body
                )
                assert len(response.content) < MAX_MCP_RESPONSE_BYTES
                task = _message(response)["result"]
                assert task["status"] == "completed"
                handles.append(task["result"]["structuredContent"]["delivery"])
            assert handles[0]["response_id"] == handles[1]["response_id"]
            response = await client.post(
                "/mcp",
                headers=_headers("tools/call", name="read_response"),
                json=_body(
                    "tools/call",
                    {
                        "name": "read_response",
                        "arguments": {"response_id": handles[0]["response_id"]},
                    },
                ),
            )
            assert len(response.content) < MAX_MCP_RESPONSE_BYTES
            assert _message(response)["result"]["structuredContent"]["success"] is True
            retained = runtime.journal.get(record.task_id, runtime.journal.local_owner)
            assert len(retained.result["content"][0]["text"]) == len(text)
