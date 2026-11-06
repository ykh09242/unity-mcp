"""Policy-free Windows bootstrap and isolated socket-failure regressions."""

import asyncio
import json
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
    guarded = runner.call_args.args[0]
    assert guarded.func == entry.run_with_wakeup_guard
    callback = guarded.args[0]
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


@pytest.mark.skipif(sys.platform != "win32", reason="Requires a Windows TCP socketpair reset")
@pytest.mark.parametrize("runner_kind", ["coroutine", "http"])
def test_windows_self_pipe_reset_stops_server_and_drains_workers(tmp_path, runner_kind):
    program = """
import asyncio
import json
import socket
import struct
import time
import sys
from contextlib import asynccontextmanager
sys.path.insert(0, 'src')
import main

loops = []
errors = []
cleaned = []
original_factory = asyncio.SelectorEventLoop

def factory():
    loop = original_factory()
    loops.append(loop)
    def bounded_baseline_handler(current_loop, context):
        errors.append(type(context.get('exception')).__name__)
        current_loop.default_exception_handler(context)
        if len(errors) == 3:
            current_loop._close_self_pipe()
            current_loop._make_self_pipe()
    loop.set_exception_handler(bounded_baseline_handler)
    return loop

asyncio.SelectorEventLoop = factory

def reset_wakeup():
    loop = asyncio.get_running_loop()
    loop._csock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('HH', 1, 0))
    loop._csock.close()

async def serve(*args, **kwargs):
    reset_wakeup()
    try:
        await asyncio.sleep(0.05)
    finally:
        await asyncio.to_thread(time.sleep, 0.01)
        cleaned.append('worker drained')

@asynccontextmanager
async def lifespan(server):
    asyncio.get_running_loop().call_later(0.05, reset_wakeup)
    try:
        yield {}
    finally:
        await asyncio.to_thread(time.sleep, 0.01)
        cleaned.append('worker drained')

server = main.UnityMCP('socket-reset-regression', lifespan=lifespan)
if sys.argv[1] == 'coroutine':
    server.run_async = serve
try:
    server.run('http', show_banner=False, host='127.0.0.1', port=0)
finally:
    print(json.dumps({'errors': errors, 'cleaned': cleaned, 'closed': loops[0].is_closed()}))
"""
    result = subprocess.run(
        [sys.executable, "-W", "error", "-c", program, runner_kind],
        env={
            **os.environ,
            "UNITY_MCP_LOG_DIR": str(tmp_path),
            "UNITY_MCP_DISABLE_TELEMETRY": "true",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "Windows event loop wakeup socket failed" in result.stderr
    assert "Exception in callback" not in result.stderr
    assert "Task was destroyed" not in result.stderr
    assert json.loads(result.stdout) == {
        "errors": [],
        "cleaned": ["worker drained"],
        "closed": True,
    }


def test_wakeup_guard_delegates_ordinary_connection_resets():
    from core.windows_event_loop import run_with_wakeup_guard

    received = []

    def previous(loop, context):
        received.append(context)

    async def serve():
        def client_callback():
            raise ConnectionResetError("a client disconnected")

        asyncio.get_running_loop().call_soon(client_callback)
        await asyncio.sleep(0)

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        loop = runner.get_loop()
        loop.set_exception_handler(previous)
        runner.run(run_with_wakeup_guard(serve))
        assert loop.get_exception_handler() is previous
    assert len(received) == 1
    assert str(received[0]["exception"]) == "a client disconnected"


def test_wakeup_guard_preserves_external_cancellation():
    from core.windows_event_loop import run_with_wakeup_guard

    async def serve():
        asyncio.current_task().cancel()
        await asyncio.sleep(0)

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        with pytest.raises(asyncio.CancelledError):
            runner.run(run_with_wakeup_guard(serve))
        assert runner.get_loop().get_exception_handler() is None


@pytest.mark.parametrize("suppress_cancellation", [False, True])
def test_repeated_wakeup_failure_does_not_cancel_cleanup_again(suppress_cancellation):
    from core.windows_event_loop import run_with_wakeup_guard

    cleaned = []

    def fail_wakeup(loop):
        loop.call_exception_handler(
            {
                "exception": ConnectionResetError("owned wakeup socket reset"),
                "handle": asyncio.Handle(loop._read_from_self, (), loop),
            }
        )

    async def serve():
        loop = asyncio.get_running_loop()
        fail_wakeup(loop)
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            if not suppress_cancellation:
                raise
        finally:
            fail_wakeup(loop)
            cleaned.append(await asyncio.to_thread(lambda: "worker drained"))

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        with pytest.raises(RuntimeError, match="Windows event loop wakeup socket failed"):
            runner.run(run_with_wakeup_guard(serve))
        assert runner.get_loop().get_exception_handler() is None
    assert cleaned == ["worker drained"]
