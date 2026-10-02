"""Exercise the console entry point, MCP handshake, and plugin command wire path."""

import json
import os
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect

from core.local_auth import LOCAL_AUTH_HEADER


@pytest.fixture
def running_server(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    token_path = tmp_path / "launch-token"
    env = {
        **os.environ,
        "DISABLE_TELEMETRY": "1",
        "UNITY_MCP_SKIP_STARTUP_CONNECT": "1",
        "UNITY_MCP_HTTP_REMOTE_HOSTED": "false",
        "UNITY_MCP_LOCAL_AUTH_TOKEN_FILE": str(token_path),
        "UNITY_MCP_LOG_DIR": str(tmp_path / "logs"),
    }
    url = f"http://127.0.0.1:{port}"
    with (tmp_path / "server.log").open("w", encoding="utf-8") as log:
        with subprocess.Popen(
            [
                sys.executable,
                "-m",
                "main",
                "--transport",
                "http",
                "--http-host",
                "127.0.0.1",
                "--http-port",
                str(port),
                "--project-scoped-tools",
            ],
            cwd=Path(__file__).resolve().parents[2],
            env=env,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        ) as process:
            try:
                with httpx.Client(timeout=0.5, trust_env=False) as client:
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        assert process.poll() is None, (
                            "Local server exited before readiness"
                        )
                        try:
                            if client.get(f"{url}/health").status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        # Bounded external-process readiness polling, not a race delay.
                        time.sleep(0.05)
                    else:
                        pytest.fail("Local server did not become ready")
                yield url, port, token_path.read_text(encoding="utf-8").strip()
            finally:
                process.terminate()
                process.wait(timeout=10)


@pytest.mark.asyncio
async def test_native_clients_work_and_browser_requests_fail_on_real_server(
    running_server,
):
    url, port, token = running_server
    headers = {LOCAL_AUTH_HEADER: token}

    # Native MCP protocol initialization and session requests retain authentication.
    async with Client(StreamableHttpTransport(f"{url}/mcp", headers=headers), mode="legacy") as mcp:
        assert await mcp.ping()
        assert any(
            tool.name == "set_active_instance" for tool in await mcp.list_tools()
        )

    # Modern clients discover and call the same authenticated tool surface.
    async with Client(StreamableHttpTransport(f"{url}/mcp", headers=headers)) as mcp:
        assert any(tool.name == "set_active_instance" for tool in await mcp.list_tools())
        selection = await mcp.call_tool("set_active_instance", {"instance": "unused"})
        assert selection.structured_content["success"] is False
        assert "sessionless" in selection.structured_content["error"]

    websocket_url = f"ws://127.0.0.1:{port}/hub/plugin"
    with pytest.raises(InvalidStatus) as denied:
        with connect(websocket_url, origin="https://attacker.example", proxy=None):
            pass
    assert denied.value.response.status_code == 403

    # A native plugin can register and receive a command for its explicit instance.
    with connect(websocket_url, additional_headers=headers, proxy=None) as plugin:
        assert json.loads(plugin.recv(timeout=5))["type"] == "welcome"
        plugin.send(
            json.dumps(
                {
                    "type": "register",
                    "project_name": "AuthWireTest",
                    "project_hash": "auth-wire-test",
                    "unity_version": "6000.0",
                }
            )
        )
        assert json.loads(plugin.recv(timeout=5))["type"] == "registered"

        with httpx.Client(timeout=10, trust_env=False) as http:
            denied_command = http.post(
                f"{url}/api/command",
                content="not-json",
                headers={"Origin": "https://attacker.example"},
            )
            assert denied_command.status_code == 403
            assert http.get(f"{url}/api/instances").status_code == 401

            with ThreadPoolExecutor(max_workers=1) as executor:
                response = executor.submit(
                    http.post,
                    f"{url}/api/command",
                    headers=headers,
                    json={
                        "type": "read_console",
                        "params": {},
                        "unity_instance": "auth-wire-test",
                    },
                )
                command = json.loads(plugin.recv(timeout=5))
                assert command["name"] == "read_console"
                plugin.send(
                    json.dumps(
                        {
                            "type": "command_result",
                            "id": command["id"],
                            "result": {"success": True, "data": "wire-response"},
                        }
                    )
                )
                assert response.result(timeout=5).json() == {
                    "success": True,
                    "data": "wire-response",
                }
