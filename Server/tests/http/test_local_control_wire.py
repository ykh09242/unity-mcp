"""Exercise the console entry point, MCP handshake, and plugin command wire path."""

from tests.timing import watchdog_timeout

import json
import os
import re
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


def _terminate_and_reap(process: subprocess.Popen) -> None:
    """Give the owned child a graceful exit, then kill and reap within a watchdog."""
    if process.poll() is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=watchdog_timeout())
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        process.wait(timeout=watchdog_timeout())


def _startup_failure_message(
    reason: str,
    command: list[str],
    exit_code: int | None,
    log_path: Path,
    token_path: Path,
    token: str,
) -> str:
    """Preserve bounded owned child output in existing pytest/JUnit failure reports."""
    limit = 8192
    try:
        with log_path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            offset = max(0, stream.tell() - limit)
            stream.seek(offset)
            raw = stream.read(limit)
        # Discard a cut first line so a credential fragment cannot escape redaction.
        if offset:
            raw = raw.partition(b"\n")[2]
        tail = "\n".join(raw.decode("utf-8", errors="replace").splitlines()[-60:])
    except OSError as error:
        tail = f"<child log unavailable: {type(error).__name__}>"
    try:
        with token_path.open(encoding="utf-8") as stream:
            published_token = stream.read(256).strip()
    except OSError:
        published_token = ""
    message = (
        f"{reason}; exit_code={exit_code}; command={json.dumps(command)}; log={log_path}\n"
        f"Sanitized child stdout/stderr tail (at most {limit} bytes / 60 lines):\n{tail or '<empty>'}"
    )
    for credential in (token, published_token):
        if credential:
            message = message.replace(credential, "[REDACTED]")
    # A child may remove its token file on exit before the parent ever reads it.
    # The production launch uses token_urlsafe(32), yielding 43 URL-safe characters.
    return re.sub(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])", "[REDACTED]", message)


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
    command = [
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
    ]
    log_path = tmp_path / "server.log"
    token = ""
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            cwd=Path(__file__).resolve().parents[2],
            env=env,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            with httpx.Client(timeout=0.5, trust_env=False) as client:
                deadline = time.monotonic() + watchdog_timeout(20)
                while time.monotonic() < deadline:
                    exit_code = process.poll()
                    if exit_code is not None:
                        raise AssertionError(
                            _startup_failure_message(
                                "Local server exited before readiness",
                                command,
                                exit_code,
                                log_path,
                                token_path,
                                token,
                            )
                        )
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
                            exit_code = process.poll()
                            if exit_code is not None:
                                raise AssertionError(
                                    _startup_failure_message(
                                        "Local server exited before readiness",
                                        command,
                                        exit_code,
                                        log_path,
                                        token_path,
                                        token,
                                    )
                                )
                            break
                    except httpx.TransportError:
                        pass
                    # Bounded external-process readiness polling, not a race delay.
                    time.sleep(0.05)
                else:
                    pytest.fail(
                        _startup_failure_message(
                            "Local server did not become ready",
                            command,
                            process.poll(),
                            log_path,
                            token_path,
                            token,
                        )
                    )
            yield url, port, token
        finally:
            failure = sys.exception()
            try:
                _terminate_and_reap(process)
            except Exception as cleanup_error:
                if failure is None:
                    raise
                failure.add_note(
                    f"Owned local server cleanup failed: {type(cleanup_error).__name__}"
                )


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


@pytest.mark.parametrize("primary_failure", [False, True])
@pytest.mark.parametrize("reap_failure", [False, True])
def test_local_child_cleanup_kills_after_grace_and_never_uses_unbounded_context_exit(
    tmp_path, controlled_startup, primary_failure, reap_failure
):
    _state, process = controlled_startup
    timeout = subprocess.TimeoutExpired("owned child", watchdog_timeout())
    process.wait.side_effect = [timeout, timeout if reap_failure else 0]

    def scenario():
        with local_server_process(tmp_path):
            if primary_failure:
                raise AssertionError("original owned fixture failure")

    if primary_failure:
        with pytest.raises(AssertionError, match="original owned fixture failure") as failure:
            scenario()
        if reap_failure:
            assert failure.value.__notes__ == ["Owned local server cleanup failed: TimeoutExpired"]
    elif reap_failure:
        with pytest.raises(subprocess.TimeoutExpired):
            scenario()
    else:
        scenario()

    process.terminate.assert_called_once()
    process.kill.assert_called_once()
    assert process.wait.call_count == 2
    assert all(
        call.kwargs == {"timeout": watchdog_timeout()} for call in process.wait.call_args_list
    )
    process.__enter__.assert_not_called()
    process.__exit__.assert_not_called()


def test_startup_exit_preserves_bounded_child_output_without_launch_credential(
    tmp_path, monkeypatch
):
    # Given: an actual owned child writes both streams and exits before readiness.
    fixture_module = tmp_path / "owned_startup_exit.py"
    fixture_module.write_text(
        "import os,sys\n"
        "from pathlib import Path\n"
        "token='Q'*43\n"
        "path=Path(os.environ['UNITY_MCP_LOCAL_AUTH_TOKEN_FILE'])\n"
        "path.write_text(token,encoding='utf-8')\n"
        "print('discarded-prefix-'+('x'*20000),flush=True)\n"
        "print('owned stdout marker',flush=True)\n"
        "print('owned stderr marker '+token,file=sys.stderr,flush=True)\n"
        "print('RuntimeError: owned startup failure',file=sys.stderr,flush=True)\n"
        "path.unlink()\n"
        "raise SystemExit(3)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    original_popen = subprocess.Popen
    launched = []

    def capture_child(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        launched.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", capture_child)
    # When
    with pytest.raises(AssertionError, match="exited before readiness") as error:
        with local_server_process(tmp_path, module="owned_startup_exit"):
            pytest.fail("An exited fixture child yielded readiness")
    # Then: one attempt, original exit code, both streams and useful exception survive.
    message = str(error.value)
    assert len(launched) == 1
    assert launched[0].returncode == 3
    assert "exit_code=3" in message
    assert "owned_startup_exit" in message
    assert "owned stdout marker" in message
    assert "owned stderr marker" in message
    assert "RuntimeError: owned startup failure" in message
    assert "[REDACTED]" in message
    assert "Q" * 43 not in message
    assert "discarded-prefix" not in message
    assert len(message) < 10000
    assert not (tmp_path / "launch-token").exists()


def test_startup_log_tail_bounds_lines_and_redacts_cached_and_published_tokens(tmp_path):
    # Given: both an earlier cached credential and a newly published owned token.
    log_path, token_path = tmp_path / "server.log", tmp_path / "launch-token"
    token_path.write_text("published-fixture-token", encoding="utf-8")
    log_path.write_text(
        "\n".join(f"row-{index}" for index in range(500))
        + "\ncached-fixture-token\npublished-fixture-token\n",
        encoding="utf-8",
    )
    # When
    message = _startup_failure_message(
        "Local server did not become ready",
        ["python", "-m", "main"],
        None,
        log_path,
        token_path,
        "cached-fixture-token",
    )
    # Then
    tail = message.split("lines):\n", 1)[1]
    assert len(tail.splitlines()) == 60
    assert "row-440\n" not in tail
    assert "row-499\n" in tail
    assert message.count("[REDACTED]") == 2
    assert "cached-fixture-token" not in message
    assert "published-fixture-token" not in message


def test_startup_byte_tail_drops_the_partial_credential_line(tmp_path):
    # Given: the byte boundary falls inside a known credential, with fewer than 60 lines.
    credential = "owned-prefix-" + "s" * 300 + "-private-credential-suffix"
    log_path = tmp_path / "server.log"
    log_path.write_text(credential + "\n" + "x" * 8000 + "\nowned-tail\n", encoding="utf-8")
    # When
    message = _startup_failure_message(
        "Local server exited before readiness",
        ["python"],
        3,
        log_path,
        tmp_path / "removed-token",
        credential,
    )
    # Then: exact-token replacement alone could not redact the surviving suffix.
    assert "private-credential-suffix" not in message
    assert "owned-tail" in message
