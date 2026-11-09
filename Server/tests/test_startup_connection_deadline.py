"""Startup's original budget bounds actual legacy locks and wire I/O."""

import asyncio
import json
import socket
import threading
import time
from unittest.mock import AsyncMock, Mock

import pytest

import main
from core.config import config
from fastmcp import FastMCP
from models.models import UnityInstanceInfo
from transport.legacy import unity_connection as uc
from transport.legacy.port_discovery import PortDiscovery


@pytest.mark.parametrize("transport,skip", [("http", "0"), ("stdio", "1"), ("stdio", "0")])
def test_cli_default_discovery_shares_startup_budget(monkeypatch, tmp_path, transport, skip):
    from contextlib import nullcontext

    monkeypatch.setattr(config, "transport_mode", config.transport_mode)
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setenv("UNITY_MCP_SKIP_STARTUP_CONNECT", skip)
    monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", "core")
    monkeypatch.setenv("UNITY_MCP_HTTP_REMOTE_HOSTED", "false")
    monkeypatch.setenv("UNITY_MCP_HTTP_HOST", "127.0.0.1")
    monkeypatch.setenv("UNITY_MCP_HTTP_PORT", "8080")
    monkeypatch.setattr(main.sys, "argv", ["main", "--transport", transport])
    monkeypatch.setattr(main, "local_http_sockets", lambda *_: nullcontext([]))
    monkeypatch.setattr(main, "local_auth_token", lambda *_: nullcontext("owned-fixture"))
    monkeypatch.setattr(main, "local_auth_token_path", lambda _: tmp_path / "token")
    monkeypatch.setattr(main, "STARTUP_CONNECT_TIMEOUT", 0.05)
    pool = Mock()
    pool.discover_all_instances.side_effect = lambda: time.sleep(0.07) or []
    monkeypatch.setattr(uc, "get_unity_connection_pool", Mock(return_value=pool))
    server = Mock()
    monkeypatch.setattr(main, "create_mcp_server", Mock(return_value=server))
    began = time.monotonic()
    main.main()
    if transport == "stdio" and skip == "0":
        pool.discover_all_instances.assert_called_once()
        assert began <= server._unity_startup_deadline <= began + 0.06
        assert server._unity_startup_deadline < time.monotonic()
    else:
        pool.discover_all_instances.assert_not_called()


def test_cli_expired_default_discovery_does_not_restart_lifespan_budget(monkeypatch):
    monkeypatch.setattr(config, "transport_mode", config.transport_mode)
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setenv("UNITY_MCP_SKIP_STARTUP_CONNECT", "0")
    monkeypatch.setenv("UNITY_MCP_HTTP_REMOTE_HOSTED", "false")
    monkeypatch.setenv("UNITY_MCP_TOOL_PROFILE", "core")
    monkeypatch.setattr(main.sys, "argv", ["main", "--transport", "stdio"])
    monkeypatch.setattr(main, "STARTUP_CONNECT_TIMEOUT", 0.02)
    monkeypatch.setattr(main.threading, "Timer", Mock())
    monkeypatch.setattr(main, "_unity_connection_pool", None)
    monkeypatch.setattr(main, "_plugin_registry", None)
    monkeypatch.setattr(uc, "_unity_connection_pool", None)
    pool = Mock(aclose=AsyncMock())
    pool.discover_all_instances.side_effect = lambda: time.sleep(0.03) or []
    monkeypatch.setattr(uc, "get_unity_connection_pool", Mock(return_value=pool))
    lifespan_factory = Mock(return_value=pool)
    monkeypatch.setattr(main, "get_unity_connection_pool", lifespan_factory)
    server = FastMCP("cli-budget")
    yielded = []

    async def lifespan():
        async with main.server_lifespan(server):
            yielded.append(True)

    monkeypatch.setattr(server, "run", lambda **_: asyncio.run(lifespan()))
    monkeypatch.setattr(main, "create_mcp_server", Mock(return_value=server))
    main.main()
    assert yielded == [True]
    pool.discover_all_instances.assert_called_once()
    lifespan_factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["pool_init", "scan", "pool", "connection"])
async def test_startup_original_deadline_bounds_lock_waits(monkeypatch, stage):
    pool = uc.UnityConnectionPool()
    target = UnityInstanceInfo(
        id="Fixture@deadbeef",
        name="Fixture",
        hash="deadbeef",
        path="/Fixture/Assets",
        port=1,
        status="running",
    )
    connection = uc.UnityConnection(port=1, instance_id=target.id, selected_target=target)
    pool._connections[target.id] = connection
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(pool, "resolve_instance", lambda _: target)
    monkeypatch.setattr(uc, "_unity_connection_pool", None)
    lock, function = {
        "pool_init": (uc._pool_init_lock, uc.get_unity_connection_pool),
        "scan": (pool._scan_lock, pool.discover_all_instances),
        "pool": (pool._pool_lock, pool.get_connection),
        "connection": (connection._conn_lock, connection.connect),
    }[stage]
    held, release = threading.Event(), threading.Event()

    def hold():
        with lock:
            held.set()
            release.wait(2)

    holder = threading.Thread(target=hold)
    holder.start()
    assert held.wait(1)
    began = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            await main._startup_worker(function, asyncio.get_running_loop().time() + 0.08)
        assert time.monotonic() - began < 0.8
    finally:
        release.set()
        holder.join(2)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["discovery", "handshake"])
async def test_startup_silent_listener_consumes_original_remaining_budget(
    monkeypatch, tmp_path, phase
):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "connection_timeout", 2)
    monkeypatch.setattr(config, "handshake_timeout", 2)
    monkeypatch.setenv("UNITY_MCP_STATUS_DIR", str(tmp_path))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(2)
        port = listener.getsockname()[1]
        accepted, release = threading.Event(), threading.Event()

        def silent():
            with listener.accept()[0] as peer:
                accepted.set()
                release.wait(2)

        worker = threading.Thread(target=silent)
        worker.start()
        (tmp_path / "unity-mcp-status-deadbeef.json").write_text(
            json.dumps({"project_path": "/Fixture/Assets", "unity_port": port})
        )
        pool = uc.UnityConnectionPool()
        target = UnityInstanceInfo(
            id="Fixture@deadbeef",
            name="Fixture",
            hash="deadbeef",
            path="/Fixture/Assets",
            port=port,
            status="running",
        )
        if phase == "handshake":
            monkeypatch.setattr(pool, "resolve_instance", lambda _: target)
        function = pool.discover_all_instances if phase == "discovery" else pool.get_connection
        deadline = asyncio.get_running_loop().time() + 0.16
        # Prior startup work has already consumed half the original budget.
        await main._startup_worker(lambda: time.sleep(0.08), deadline)
        began = time.monotonic()
        try:
            with pytest.raises(TimeoutError):
                await main._startup_worker(function, deadline)
            assert accepted.is_set()
            assert time.monotonic() - began < 0.2
            assert not pool._connections
        finally:
            release.set()
            worker.join(2)
            pool.disconnect_all()
