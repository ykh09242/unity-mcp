"""Policy-free Windows bootstrap contracts, without opening a server."""

import asyncio
import os
import subprocess
import sys
from unittest.mock import AsyncMock, Mock

import pytest
from fastmcp import FastMCP


@pytest.fixture
def entry():
    before = dict(os.environ)
    try:
        import main

        yield main
    finally:
        os.environ.clear()
        os.environ.update(before)


def test_import_does_not_set_global_loop_policy(tmp_path):
    program = """
import asyncio
import sys
sys.path.insert(0, 'src')
def forbidden(*args, **kwargs):
    raise AssertionError('import must not configure a global loop policy')
asyncio.set_event_loop_policy = forbidden
asyncio.WindowsSelectorEventLoopPolicy = forbidden
import main
"""
    result = subprocess.run(
        [sys.executable, "-W", "error", "-c", program],
        env={
            **os.environ,
            "UNITY_MCP_LOG_DIR": str(tmp_path),
            "UNITY_MCP_DISABLE_TELEMETRY": "true",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "transport, kwargs",
    [
        (None, {}),
        ("stdio", {"show_banner": False}),
        ("http", {"host": "127.0.0.1", "port": 8099, "show_banner": True}),
    ],
)
def test_windows_run_passes_factory_only_to_anyio(entry, monkeypatch, transport, kwargs):
    monkeypatch.setattr(entry.sys, "platform", "win32")
    runner = Mock(return_value=object())
    monkeypatch.setattr(entry.anyio, "run", runner)
    server = entry.UnityMCP("loop-contract")
    assert server.run(transport, **kwargs) is None
    callback = runner.call_args.args[0]
    assert callback.func == server.run_async
    assert callback.args == (transport,)
    assert callback.keywords == {"show_banner": None, **kwargs}
    assert runner.call_args.kwargs == {
        "backend_options": {"loop_factory": asyncio.SelectorEventLoop},
    }


def test_non_windows_run_delegates_unchanged(entry, monkeypatch):
    monkeypatch.setattr(entry.sys, "platform", "linux")
    delegated = Mock(return_value=object())
    monkeypatch.setattr(FastMCP, "run", delegated)
    server = entry.UnityMCP("loop-contract")
    assert server.run("http", show_banner=False, port=8099) is delegated.return_value
    delegated.assert_called_once_with("http", show_banner=False, port=8099)


def test_cli_bootstrap_runs_and_closes_selector_loop(entry, monkeypatch):
    monkeypatch.setattr(entry.sys, "platform", "win32")
    monkeypatch.setattr(entry.sys, "argv", ["main", "--project-scoped-tools"])
    for field in (
        "transport_mode",
        "http_remote_hosted",
        "http_behind_tls_proxy",
        "api_key_validation_url",
        "api_key_login_url",
        "api_key_cache_ttl",
        "api_key_service_token_header",
        "api_key_service_token",
    ):
        monkeypatch.setattr(entry.config, field, getattr(entry.config, field))
    for name in ("UNITY_MCP_HTTP_HOST", "UNITY_MCP_HTTP_PORT"):
        monkeypatch.setenv(name, os.environ.get(name, ""))
    for name in ("UNITY_MCP_HTTP_REMOTE_HOSTED", "UNITY_MCP_HTTP_BEHIND_TLS_PROXY"):
        monkeypatch.delenv(name, raising=False)
    selector_type = asyncio.SelectorEventLoop
    factory = Mock(wraps=selector_type)
    monkeypatch.setattr(entry.asyncio, "SelectorEventLoop", factory)
    loops = []

    async def short_bootstrap(*args, **kwargs):
        loop = asyncio.get_running_loop()
        assert isinstance(loop, selector_type)
        await asyncio.sleep(0)
        loops.append(loop)

    server = entry.UnityMCP("loop-contract")
    server.run_async = AsyncMock(side_effect=short_bootstrap)
    monkeypatch.setattr(entry, "create_mcp_server", Mock(return_value=server))
    entry.main()
    server.run_async.assert_awaited_once_with("stdio", show_banner=None)
    factory.assert_called_once_with()
    assert len(loops) == 1 and loops[0].is_closed()
