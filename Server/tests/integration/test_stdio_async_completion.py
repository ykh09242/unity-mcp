"""Async completion retains dispatched connection without event-loop rediscovery."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
from unittest.mock import AsyncMock

import pytest

from core.config import config
from models.models import MCPResponse
from transport.legacy.port_discovery import PortDiscovery
import transport.legacy.unity_connection as uc


@pytest.fixture
def connection_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.delenv("UNITY_MCP_DEFAULT_INSTANCE", raising=False)
    monkeypatch.setenv("UNITY_MCP_STATUS_DIR", str(tmp_path))
    (tmp_path / "unity-mcp-status-deadbeef.json").write_text(json.dumps({
        "project_path": "/Owned/Main/Assets", "unity_port": 6400}), encoding="utf-8")
    monkeypatch.setattr(uc.UnityConnection, "connect", lambda *a, **kw: True)
    probes = []
    monkeypatch.setattr(PortDiscovery, "_try_probe_unity_mcp", lambda port: probes.append(port) or True)
    pool = uc.UnityConnectionPool()
    monkeypatch.setattr(uc, "get_unity_connection_pool", lambda: pool)
    conn = pool.get_connection("Main@deadbeef")
    monkeypatch.setattr(conn, "send_command", lambda *a, **kw: {"success": True, "data": "same reply"})
    probes.clear()
    return pool, conn, probes


@pytest.mark.asyncio
async def test_completed_command_does_not_rediscover_expired_target(connection_environment, monkeypatch):
    pool, conn, probes = connection_environment
    def send(*args, **kwargs):
        # Model a >5s command without adding a long delay to the regression.
        pool._target_refreshes[conn.instance_id] = time.time() - 6
        return {"success": True, "data": "same reply"}
    monkeypatch.setattr(conn, "send_command", send)
    result = await uc.async_send_command_with_retry("manage_scene", {"action": "get_active"}, instance_id=conn.instance_id)
    assert result == {"success": True, "data": "same reply"}
    assert probes == [], "A completed command re-ran discovery just to inspect its resync flag"


@pytest.mark.asyncio
async def test_async_discovery_probes_only_in_executor_when_command_expires_cache(connection_environment, monkeypatch):
    pool, conn, _ = connection_environment
    event_loop_thread = threading.get_ident()
    probe_threads = []
    def probe(port):
        probe_threads.append(threading.get_ident())
        return True
    def send(*args, **kwargs):
        pool._target_refreshes[conn.instance_id] = time.time() - 6
        return {"success": True}
    monkeypatch.setattr(conn, "send_command", send)
    monkeypatch.setattr(PortDiscovery, "_try_probe_unity_mcp", probe)
    # Force dispatch discovery too, so this checks both the required worker
    # probe and the absence of a second probe on async completion.
    pool._target_refreshes[conn.instance_id] = time.time() - 6
    assert (await uc.async_send_command_with_retry("manage_scene", {}, instance_id=conn.instance_id))["success"]
    assert len(probe_threads) == 1
    assert event_loop_thread not in probe_threads


@pytest.mark.asyncio
@pytest.mark.parametrize("selector", [None, "Main", "dead", "6400", "/Owned/Main/Assets"])
async def test_resync_pins_actual_dispatched_target_for_implicit_and_alias_selection(connection_environment, monkeypatch, selector):
    pool, conn, _ = connection_environment
    conn._needs_tool_resync = True
    resync = AsyncMock()
    monkeypatch.setattr(uc, "_resync_tools_after_reconnect", resync)
    assert (await uc.async_send_command_with_retry("manage_scene", {}, instance_id=selector))["success"]
    await asyncio.sleep(0)
    resync.assert_awaited_once_with("Main@deadbeef")
    assert conn._needs_tool_resync is False


@pytest.mark.asyncio
async def test_resync_does_not_reselect_after_another_editor_appears(connection_environment, monkeypatch):
    pool, conn, _ = connection_environment
    conn._needs_tool_resync = True
    resync = AsyncMock()
    monkeypatch.setattr(uc, "_resync_tools_after_reconnect", resync)
    def send(*args, **kwargs):
        # Dispatch already selected Main. Completion must not consult ambient
        # selection again when another Editor joins during that command.
        monkeypatch.setattr(pool, "get_connection", lambda *a, **kw: pytest.fail("completion reselected instance"))
        return {"success": True}
    monkeypatch.setattr(conn, "send_command", send)
    assert (await uc.async_send_command_with_retry("manage_scene", {}))["success"]
    await asyncio.sleep(0)
    resync.assert_awaited_once_with("Main@deadbeef")


def test_resync_flag_claim_is_atomic_without_waiting_for_socket_io():
    conn = uc.UnityConnection(port=6400, instance_id="Main@deadbeef")
    conn._needs_tool_resync = True
    with ThreadPoolExecutor(max_workers=4) as executor:
        conn._io_lock.acquire()
        try:
            futures = [executor.submit(conn.claim_tool_resync) for _ in range(4)]
            claims = [future.result(timeout=0.5) for future in futures]
        finally:
            # Release before executor shutdown, including when a regressed
            # claim blocks on this lock and the future times out.
            conn._io_lock.release()
    assert sum(claims) == 1
    assert conn._needs_tool_resync is False


@pytest.mark.asyncio
async def test_parallel_async_commands_schedule_one_resync(connection_environment, monkeypatch):
    _, conn, _ = connection_environment
    conn._needs_tool_resync = True
    resync = AsyncMock()
    monkeypatch.setattr(uc, "_resync_tools_after_reconnect", resync)
    results = await asyncio.gather(*[uc.async_send_command_with_retry("manage_scene", {}, instance_id=conn.instance_id) for _ in range(4)])
    assert all(result["success"] for result in results)
    await asyncio.sleep(0)
    resync.assert_awaited_once_with(conn.instance_id)


@pytest.mark.parametrize("response", [{"success": True}, MCPResponse(success=False, error="reloading", data={"reason": "reloading"})])
def test_public_sync_helper_keeps_response_identity_and_pending_resync(connection_environment, monkeypatch, response):
    _, conn, _ = connection_environment
    conn._needs_tool_resync = True
    monkeypatch.setattr(conn, "send_command", lambda *a, **kw: response)
    assert uc.send_command_with_retry("manage_scene", {}, instance_id=conn.instance_id, max_retries=0) is response
    assert conn._needs_tool_resync is True


@pytest.mark.asyncio
async def test_outcome_unknown_response_is_preserved_without_foreground_replay(connection_environment, monkeypatch):
    _, conn, probes = connection_environment
    conn._needs_tool_resync = True
    response = MCPResponse(success=False, error="reply lost", hint="inspect_state_before_retry", data={"reason": "outcome_unknown"})
    calls = []
    def send(command, *args, **kwargs):
        calls.append(command)
        return response
    monkeypatch.setattr(conn, "send_command", send)
    resync = AsyncMock()
    monkeypatch.setattr(uc, "_resync_tools_after_reconnect", resync)
    assert await uc.async_send_command_with_retry("manage_scene", {}, instance_id=conn.instance_id) is response
    await asyncio.sleep(0)
    assert calls == ["manage_scene"] and probes == []
    # Keep the existing background-read behavior, without re-sending foreground work.
    resync.assert_awaited_once_with(conn.instance_id)


@pytest.mark.asyncio
async def test_send_exception_keeps_pending_resync_without_extra_lookup(connection_environment, monkeypatch):
    _, conn, probes = connection_environment
    conn._needs_tool_resync = True
    resync = AsyncMock()
    monkeypatch.setattr(uc, "_resync_tools_after_reconnect", resync)
    def send(*args, **kwargs):
        raise ConnectionError("failed before response")
    monkeypatch.setattr(conn, "send_command", send)
    result = await uc.async_send_command_with_retry("manage_scene", {}, instance_id=conn.instance_id)
    assert isinstance(result, MCPResponse) and not result.success
    assert result.error == "failed before response"
    assert conn._needs_tool_resync is True
    assert probes == []
    resync.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_worker_keeps_pending_resync_for_later_command(connection_environment, monkeypatch):
    _, conn, _ = connection_environment
    conn._needs_tool_resync = True
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    resync = AsyncMock()
    monkeypatch.setattr(uc, "_resync_tools_after_reconnect", resync)
    def send(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        finished.set()
        return {"success": True}
    monkeypatch.setattr(conn, "send_command", send)
    task = asyncio.create_task(uc.async_send_command_with_retry("manage_scene", {}, instance_id=conn.instance_id))
    loop = asyncio.get_running_loop()
    try:
        assert await loop.run_in_executor(None, entered.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
    assert await loop.run_in_executor(None, finished.wait, 1)
    await asyncio.sleep(0)
    assert conn._needs_tool_resync is True
    resync.assert_not_awaited()
