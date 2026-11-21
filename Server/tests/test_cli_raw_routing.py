"""Verify the documented native command and composed report at the HTTP boundary."""

import json

import httpx
import httpx2
import pytest
from cli.main import cli
from cli.utils import connection, doctor_connection
from click.testing import CliRunner

TARGET = "MyGame@0123456789abcdef"
RUN_ID = "0123456789abcdef0123456789abcdef"


@pytest.mark.parametrize(
    "args,expected",
    [
        (
            ["raw", "manage_scene", '{"action": "get_active"}'],
            ("/api/command", "manage_scene", {"action": "get_active"}, None),
        ),
        (
            [
                "--no-project-context",
                "--instance",
                TARGET,
                "verification",
                "report",
                "--options",
                json.dumps({"run_id": RUN_ID}, separators=(",", ":")),
            ],
            (
                "/mcp",
                "manage_verification",
                {"action": "report", "options": {"run_id": RUN_ID}},
                TARGET,
            ),
        ),
    ],
)
def test_documented_examples_send_native_requests_or_mcp_tool_calls(
    args, expected, monkeypatch, tmp_path
):
    # Given: both HTTP surfaces can receive requests, and the MCP SDK negotiates normally.
    calls = []

    def native_request(request):
        body = json.loads(request.content)
        calls.append((request.url.path, body["type"], body["params"], body.get("unity_instance")))
        return httpx.Response(200, json={"success": True, "data": {}})

    def mcp_request(request):
        if request.method == "GET":
            return httpx2.Response(405)
        body = json.loads(request.content)
        match body["method"]:
            case "initialize":
                result = {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "unity-mcp-fixture", "version": "1.0"},
                }
            case "notifications/initialized":
                return httpx2.Response(202)
            case "tools/list":
                result = {
                    "tools": [{"name": "manage_verification", "inputSchema": {"type": "object"}}]
                }
            case "tools/call":
                params = body["params"]
                calls.append(
                    (
                        request.url.path,
                        params["name"],
                        params["arguments"],
                        params["_meta"]["unity_instance"],
                    )
                )
                result = {"content": [], "structuredContent": {"success": True, "data": {}}}
            case _:
                return httpx2.Response(400)
        return httpx2.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    monkeypatch.setattr(
        connection,
        "_http_client",
        lambda _config: httpx.AsyncClient(transport=httpx.MockTransport(native_request)),
    )
    monkeypatch.setattr(
        doctor_connection.httpx2,
        "AsyncHTTPTransport",
        lambda **_: httpx2.MockTransport(mcp_request),
    )
    monkeypatch.setenv("UNITY_MCP_LOCAL_AUTH_TOKEN", "fixture-token")
    for name in ("UNITY_MCP_HOST", "UNITY_MCP_HTTP_PORT", "UNITY_MCP_FORMAT", "UNITY_MCP_INSTANCE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)

    # When: the published CLI example is executed from a directory without project context.
    result = CliRunner().invoke(cli, args)

    # Then: the resolved request reaches the correct transport exactly once.
    assert result.exit_code == 0, (result.output, result.exception)
    assert calls == [expected]
