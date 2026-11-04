"""Credential lifecycle and native client discovery without real user files."""

import os
import socket
from pathlib import Path

import httpcore
import httpx
import pytest

from cli.utils.config import CLIConfig
from cli.utils.connection import (
    UnityConnectionError,
    list_custom_tools,
    list_unity_instances,
    send_command,
)
from core.local_auth import (
    LOCAL_AUTH_FILE_ENV,
    LOCAL_AUTH_HEADER,
    LOCAL_AUTH_TOKEN_ENV,
    local_auth_token,
    local_auth_token_path,
    local_http_sockets,
    read_local_auth_token,
)


@pytest.fixture
def token_path(tmp_path, monkeypatch):
    path = tmp_path / "auth" / "token-8080"
    monkeypatch.setenv(LOCAL_AUTH_FILE_ENV, str(path))
    monkeypatch.delenv(LOCAL_AUTH_TOKEN_ENV, raising=False)
    return path


def test_new_launch_rotates_token_and_removes_file_on_exit(token_path):
    with local_auth_token(8080) as first:
        assert token_path.read_text() == first
        assert len(first) == 43  # 32 cryptographically random bytes, base64url encoded.
    assert not token_path.exists()
    with local_auth_token(8080) as second:
        assert second != first
        assert read_local_auth_token("localhost", 8080) == second
        if os.name != "nt":
            assert token_path.stat().st_mode & 0o777 == 0o600
    assert not token_path.exists()


def test_shutdown_does_not_remove_newer_launch_file(token_path):
    with local_auth_token(8080):
        token_path.write_text("newer-test-launch")
    assert token_path.read_text() == "newer-test-launch"


def test_listener_is_owned_until_context_exit():
    with local_http_sockets("127.0.0.1", 0) as listeners:
        port = listeners[0].getsockname()[1]
        with pytest.raises(OSError):
            with local_http_sockets("127.0.0.1", port):
                pass
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", port))


def test_localhost_listeners_share_an_ephemeral_port():
    with local_http_sockets("localhost", 0) as listeners:
        assert len({listener.getsockname()[1] for listener in listeners}) == 1
        assert all(listener.getsockname()[1] != 0 for listener in listeners)


def test_partial_bind_failure_releases_previously_bound_socket(monkeypatch):
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        with socket.socket() as candidate:
            candidate.bind(("127.0.0.1", 0))
            free_port = candidate.getsockname()[1]
        addresses = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", free_port)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", occupied.getsockname()),
        ]
        monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: addresses)
        with pytest.raises(OSError):
            with local_http_sockets("localhost", free_port):
                pass
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", free_port))


def test_cli_does_not_automatically_disclose_local_token_to_remote_host(tmp_path, monkeypatch):
    monkeypatch.delenv(LOCAL_AUTH_FILE_ENV, raising=False)
    monkeypatch.delenv(LOCAL_AUTH_TOKEN_ENV, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = local_auth_token_path(8080)
    path.parent.mkdir(parents=True)
    path.write_text("local-test-token")
    assert read_local_auth_token("untrusted.example", 8080) is None
    assert read_local_auth_token("127.0.0.1", 8080) == "local-test-token"
    assert read_local_auth_token("::1", 8080) == "local-test-token"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["command", "instances", "tools"])
async def test_native_cli_attaches_current_token_for_every_control_request(
    token_path, monkeypatch, operation
):
    # Given a real HTTP client whose wire transport checks the credential.
    token_path.parent.mkdir()
    token_path.write_text("current-test-launch")
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers[LOCAL_AUTH_HEADER] == "current-test-launch"
        return httpx.Response(200, json={"success": True, "instances": []})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **_options: client_type(transport=httpx.MockTransport(handle))
    )
    # When any of the supported CLI paths sends a request.
    cfg = CLIConfig()
    if operation == "command":
        await send_command("read_console", {}, cfg)
    elif operation == "instances":
        await list_unity_instances(cfg)
    else:
        await list_custom_tools(cfg)
    # Then authentication reached the actual HTTP request.
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["command", "instances", "tools"])
@pytest.mark.parametrize(
    ("host", "next_hop"),
    [
        ("127.0.0.1", "127.0.0.1"),
        ("LOCALHOST", "localhost"),
        ("[::1]", "::1"),
        ("remote.invalid", "proxy.invalid"),
    ],
)
async def test_cli_loopback_control_never_routes_its_token_to_an_environment_proxy(
    monkeypatch, operation, host, next_hop
):
    # Stop after actual HTTPX proxy selection and before opening a socket.
    for name in ("http_proxy", "HTTP_PROXY"):
        monkeypatch.setenv(name, "http://proxy.invalid:8181")
    for name in ("no_proxy", "NO_PROXY"):
        monkeypatch.setenv(name, "unrelated.invalid")
    monkeypatch.setenv(LOCAL_AUTH_TOKEN_ENV, "synthetic-launch")
    selected = []

    async def stop_connect(_backend, host, port, **_options):
        selected.append((host, port))
        raise httpcore.ConnectError("test network boundary")

    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", stop_connect)
    cfg = CLIConfig(host=host, port=8080)
    with pytest.raises(UnityConnectionError):
        if operation == "command":
            await send_command("read_console", {}, cfg)
        elif operation == "instances":
            await list_unity_instances(cfg)
        else:
            await list_custom_tools(cfg)
    assert selected == [(next_hop, 8181 if next_hop == "proxy.invalid" else 8080)]
