"""Exercise the console entry point, MCP handshake, and plugin command wire path."""

from tests.timing import watchdog_timeout

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect

from core.local_auth import LOCAL_AUTH_HEADER


@contextmanager
def local_server_process(
    tmp_path: Path, *, module: str = "main", validation_url: str | None = None
) -> Iterator[tuple[str, int, str]]:
    """Start an isolated production server using the selected worktree's source."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    token_path = tmp_path / "launch-token"
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            filter(
                None, (str(Path(__file__).resolve().parents[2] / "src"), os.getenv("PYTHONPATH"))
            )
        ),
        "DISABLE_TELEMETRY": "1",
        "UNITY_MCP_SKIP_STARTUP_CONNECT": "1",
        "UNITY_MCP_HTTP_REMOTE_HOSTED": "false",
        "UNITY_MCP_LOCAL_AUTH_TOKEN_FILE": str(token_path),
        "UNITY_MCP_LOG_DIR": str(tmp_path / "logs"),
    }
    url = f"http://127.0.0.1:{port}"
    remote_args = (
        [
            "--http-remote-hosted",
            "--http-behind-tls-proxy",
            "--api-key-validation-url",
            validation_url,
        ]
        if validation_url is not None
        else []
    )
    with (tmp_path / "server.log").open("w", encoding="utf-8") as log:
        with subprocess.Popen(
            [
                sys.executable,
                "-m",
                module,
                "--transport",
                "http",
                "--http-host",
                "127.0.0.1",
                "--http-port",
                str(port),
                "--project-scoped-tools",
                *remote_args,
            ],
            cwd=Path(__file__).resolve().parents[2],
            env=env,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        ) as process:
            try:
                with httpx.Client(timeout=0.5, trust_env=False) as client:
                    deadline = time.monotonic() + watchdog_timeout(20)
                    while time.monotonic() < deadline:
                        assert process.poll() is None, "Local server exited before readiness"
                        try:
                            token = ""
                            if validation_url is None:
                                try:
                                    token = token_path.read_text(encoding="utf-8").strip()
                                except FileNotFoundError:
                                    pass  # The owned launch has not published its credential yet.
                            # The free-port handoff can race another listener. Public
                            # health alone cannot prove that this is our local child.
                            if (
                                (validation_url is not None or token)
                                and client.get(f"{url}/health").status_code == 200
                                and (
                                    validation_url is not None
                                    or (
                                        client.get(f"{url}/api/instances").status_code == 401
                                        and client.get(
                                            f"{url}/api/instances",
                                            headers={LOCAL_AUTH_HEADER: token},
                                        ).status_code
                                        == 200
                                    )
                                )
                            ):
                                assert process.poll() is None, (
                                    "Local server exited before readiness"
                                )
                                break
                        except httpx.TransportError:
                            pass
                        # Bounded external-process readiness polling, not a race delay.
                        time.sleep(0.05)
                    else:
                        pytest.fail("Local server did not become ready")
                yield url, port, token
            finally:
                process.terminate()
                process.wait(timeout=watchdog_timeout())


@pytest.fixture
def running_server(tmp_path):
    """Keep the plugin registration scenario's server isolated to its test."""
    with local_server_process(tmp_path) as server:
        yield server


@pytest.mark.asyncio
async def test_native_clients_work_and_browser_requests_fail_on_real_server(
    running_server,
):
    url, port, token = running_server
    headers = {LOCAL_AUTH_HEADER: token}

    # Native MCP protocol initialization and session requests retain authentication.
    async with Client(StreamableHttpTransport(f"{url}/mcp", headers=headers), mode="legacy") as mcp:
        assert await mcp.ping()
        assert any(tool.name == "set_active_instance" for tool in await mcp.list_tools())

    # Modern clients discover and call the same authenticated tool surface.
    async with Client(StreamableHttpTransport(f"{url}/mcp", headers=headers)) as mcp:
        assert any(tool.name == "set_active_instance" for tool in await mcp.list_tools())
        selection = await mcp.call_tool(
            "set_active_instance", {"instance": "unused"}, raise_on_error=False
        )
        assert selection.is_error is True
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
                assert response.result(timeout=watchdog_timeout()).json() == {
                    "success": True,
                    "data": "wire-response",
                }


@pytest.fixture
def controlled_startup(tmp_path, monkeypatch):
    """Expose readiness authority transitions without sockets or child processes."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    state = SimpleNamespace(
        owned=False, exit_code=None, requests=[], token="owned-test-launch-token"
    )
    process = MagicMock()
    process.__enter__.return_value = process
    process.__exit__.return_value = False
    process.poll.side_effect = lambda: state.exit_code
    monkeypatch.setattr(subprocess, "Popen", lambda *_, **__: process)
    actual_client = httpx.Client

    def respond(request):
        state.requests.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "healthy"})
        if request.url.path == "/api/instances":
            accepted = state.owned and request.headers.get(LOCAL_AUTH_HEADER) == state.token
            return httpx.Response(200 if accepted else 401, json={"success": accepted})
        raise AssertionError(f"Unexpected readiness endpoint: {request.url.path}")

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: actual_client(transport=httpx.MockTransport(respond), **kwargs),
    )

    def publish_owner(_):
        (tmp_path / "launch-token").write_text(state.token, encoding="utf-8")
        state.owned = True

    monkeypatch.setattr(time, "sleep", publish_owner)
    return state, process


def test_local_readiness_waits_for_launch_credential_before_trusting_health(
    tmp_path, controlled_startup
):
    state, process = controlled_startup
    with local_server_process(tmp_path) as (_, _, token):
        assert state.owned, "Public health from an unowned listener certified readiness"
        assert token == state.token
        assert any(request.url.path == "/api/instances" for request in state.requests)
    process.terminate.assert_called_once()
    process.wait.assert_called_once()


def test_local_readiness_rejects_foreign_listener_until_owned_token_is_accepted(
    tmp_path, controlled_startup
):
    state, process = controlled_startup
    (tmp_path / "launch-token").write_text(state.token, encoding="utf-8")
    with local_server_process(tmp_path):
        assert state.owned, "Foreign public health bypassed the launch-token ownership probe"
        authenticated = [
            request
            for request in state.requests
            if request.url.path == "/api/instances" and LOCAL_AUTH_HEADER in request.headers
        ]
        assert len(authenticated) == 2
        assert all(
            request.headers.get(LOCAL_AUTH_HEADER) == state.token for request in authenticated
        )
    process.terminate.assert_called_once()
    process.wait.assert_called_once()


def test_local_readiness_does_not_yield_after_child_exits(tmp_path, controlled_startup):
    state, process = controlled_startup
    state.owned = True
    (tmp_path / "launch-token").write_text(state.token, encoding="utf-8")

    def exit_after_health():
        if state.requests:
            state.exit_code = 1
        return state.exit_code

    process.poll.side_effect = exit_after_health
    with pytest.raises(AssertionError, match="exited before readiness"):
        with local_server_process(tmp_path):
            pytest.fail("Readiness yielded an exited child")
    process.wait.assert_called_once()


def test_remote_readiness_does_not_require_a_local_launch_token(tmp_path, controlled_startup):
    state, process = controlled_startup
    with local_server_process(tmp_path, validation_url="https://owned.invalid/validate") as (
        _,
        _,
        token,
    ):
        assert token == ""
        assert not (tmp_path / "launch-token").exists()
        assert [request.url.path for request in state.requests] == ["/health"]
    process.terminate.assert_called_once()
    process.wait.assert_called_once()
