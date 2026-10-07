"""Credential lifecycle and native client discovery without real user files."""

import os
from pathlib import Path

import httpx
import pytest

from cli.utils.config import CLIConfig
from cli.utils.connection import list_custom_tools, list_unity_instances, send_command
from core.local_auth import (
    LOCAL_AUTH_FILE_ENV,
    LOCAL_AUTH_HEADER,
    LOCAL_AUTH_TOKEN_ENV,
    local_auth_token,
    local_auth_token_path,
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
        httpx, "AsyncClient", lambda: client_type(transport=httpx.MockTransport(handle))
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
