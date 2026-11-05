"""Credential lifecycle and native client discovery without real user files."""

import os
import socket
import multiprocessing
from contextlib import ExitStack
from itertools import count
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


@pytest.fixture(autouse=True)
def synthetic_tokens(monkeypatch):
    sequence = count()
    monkeypatch.setattr(
        "core.local_auth.secrets.token_urlsafe",
        lambda _bytes: f"synthetic-test-token-{next(sequence)}".ljust(43, "x"),
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


@pytest.mark.parametrize("second_port", [8080, 8081])
def test_live_token_file_cannot_be_replaced_even_across_ports(token_path, second_port):
    with local_auth_token(8080) as owner:
        with pytest.raises(BlockingIOError):
            with local_auth_token(second_port):
                pytest.fail("A competing launch published another token")
        assert token_path.read_text() == owner
    assert not token_path.exists()
    with local_auth_token(second_port) as successor:
        assert token_path.read_text() == successor


@pytest.mark.parametrize("first_host,second_host", [("127.0.0.1", "::1"), ("::1", "127.0.0.1")])
@pytest.mark.parametrize("owner_exits_first", [False, True])
def test_distinct_loopback_listeners_cannot_replace_shared_token(
    token_path, first_host, second_host, owner_exits_first
):
    with ExitStack() as owner:
        try:
            first = owner.enter_context(local_http_sockets(first_host, 0))
        except OSError as error:
            pytest.skip(f"Loopback family unavailable: {error}")
        port = first[0].getsockname()[1]
        token = owner.enter_context(local_auth_token(port))
        with ExitStack() as contender:
            try:
                contender.enter_context(local_http_sockets(second_host, port))
            except OSError as error:
                pytest.skip(f"Separate loopback family unavailable: {error}")
            with pytest.raises(BlockingIOError):
                contender.enter_context(local_auth_token(port))
            assert token_path.read_text() == token
            if owner_exits_first:
                owner.close()
                assert not token_path.exists()
                successor = contender.enter_context(local_auth_token(port))
                assert token_path.read_text() == successor
        if not owner_exits_first:
            assert token_path.read_text() == token
    assert not token_path.exists()
    with local_http_sockets(second_host, port):
        with local_auth_token(port) as successor:
            assert token_path.read_text() == successor


def test_distinct_explicit_files_can_be_owned_together(token_path, monkeypatch):
    with local_auth_token(8080) as first:
        other = token_path.with_name("other-token")
        monkeypatch.setenv(LOCAL_AUTH_FILE_ENV, str(other))
        with local_auth_token(8080) as second:
            assert token_path.read_text() == first
            assert other.read_text() == second
        assert not other.exists()
        assert token_path.read_text() == first


def test_relative_token_path_alias_cannot_bypass_ownership(token_path, monkeypatch):
    with local_auth_token(8080) as owner:
        monkeypatch.chdir(token_path.parent.parent)
        monkeypatch.setenv(LOCAL_AUTH_FILE_ENV, "auth/../auth/" + token_path.name)
        with pytest.raises(BlockingIOError):
            with local_auth_token(8081):
                pytest.fail("Relative path aliases must share ownership")
        assert token_path.read_text() == owner


@pytest.mark.parametrize("failure", ["generate", "temporary", "publish", "body", "cleanup"])
def test_exception_releases_token_file_ownership(token_path, monkeypatch, failure):
    with monkeypatch.context() as patch:
        if failure in {"generate", "temporary", "publish"}:

            def fail_startup(*_args, **_kwargs):
                raise OSError("synthetic startup failure")

            seam = {
                "generate": "core.local_auth.secrets.token_urlsafe",
                "temporary": "core.local_auth.tempfile.mkstemp",
                "publish": "core.local_auth.os.replace",
            }[failure]
            patch.setattr(seam, fail_startup)
        elif failure == "cleanup":

            def fail_read(*_args, **_kwargs):
                raise OSError("synthetic cleanup failure")

            patch.setattr(Path, "read_text", fail_read)
        with pytest.raises(OSError):
            with local_auth_token(8080):
                if failure == "body":
                    raise OSError("synthetic server failure")
    assert not list(token_path.parent.glob(".token-*"))
    with local_auth_token(8080) as successor:
        assert token_path.read_text() == successor


def _hold_token_file(path, connection):
    os.environ[LOCAL_AUTH_FILE_ENV] = path
    # Child processes bypass pytest fixtures; never generate real credentials here.
    from core import local_auth

    local_auth.secrets.token_urlsafe = lambda _bytes: "synthetic-child-token"
    with local_auth_token(8080) as token:
        connection.send(token)
        connection.recv()
    connection.close()


@pytest.mark.parametrize("crash", [False, True])
def test_token_file_process_contention_and_release(token_path, crash):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_hold_token_file, args=(str(token_path), child))
    process.start()
    child.close()
    try:
        assert parent.poll(15), "Child did not acquire token ownership"
        owner = parent.recv()
        with pytest.raises(BlockingIOError):
            with local_auth_token(8081):
                pytest.fail("Another process owns this token file")
        assert token_path.read_text() == owner
        if crash:
            process.terminate()
        else:
            parent.send("stop")
        process.join(15)
        assert not process.is_alive()
        assert token_path.exists() == crash
        with local_auth_token(8081) as successor:
            assert token_path.read_text() == successor
    finally:
        if process.is_alive():
            process.terminate()
        process.join(15)
        parent.close()
        process.close()


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
