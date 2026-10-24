"""Reconnect metadata belongs to one pool generation and actual worker lifetime."""

import asyncio  # noqa: ANYIO_OK -- exercise the transport's existing asyncio lifetime.
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.timing import await_signal, watchdog_timeout
from transport.legacy import unity_connection as uc


@pytest.fixture
def pool_connection(monkeypatch):
    pool = uc.UnityConnectionPool()
    target = SimpleNamespace(id="Main@deadbeef", port=6400)
    monkeypatch.setattr(pool, "resolve_instance", lambda _: target)
    monkeypatch.setattr(uc.UnityConnection, "connect", lambda _: True)
    conn = pool.get_connection(target.id)
    monkeypatch.setattr(uc, "get_unity_connection_pool", lambda: pool)
    return pool, conn


@pytest.mark.asyncio
async def test_shutdown_cancels_resync_before_disposal_and_rejects_stale_schedule(
    pool_connection, monkeypatch
):
    pool, conn = pool_connection
    started = asyncio.Event()
    published = []
    disconnected = Mock()
    monkeypatch.setattr(conn, "disconnect", disconnected)

    async def stalled(_):
        started.set()
        try:
            await asyncio.Event().wait()
            published.append("old")
        finally:
            disconnected.assert_not_called()

    monkeypatch.setattr(uc, "_resync_tools_after_reconnect", stalled)
    assert pool.schedule_tool_resync(conn)
    await await_signal(started, "resync started")
    await pool.aclose()
    disconnected.assert_called_once()
    assert published == []
    assert pool.schedule_tool_resync(conn) is False
    with pytest.raises(ConnectionError, match="retired"):
        pool.get_connection(conn.instance_id)


@pytest.mark.asyncio
async def test_cancelled_resync_retains_running_executor_until_real_completion(
    pool_connection, monkeypatch
):
    pool, conn = pool_connection
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    finished = threading.Event()
    published = []
    disconnected = Mock()
    monkeypatch.setattr(conn, "disconnect", disconnected)

    def send(*_, **__):
        loop.call_soon_threadsafe(started.set)
        try:
            assert release.wait(watchdog_timeout())
            return {"success": True}, conn
        finally:
            finished.set()

    async def resync(instance_id):
        await uc.async_send_command_with_retry("get_tool_states", {}, instance_id=instance_id)
        published.append("old")

    monkeypatch.setattr(uc, "_send_command_with_retry", send)
    monkeypatch.setattr(uc, "_resync_tools_after_reconnect", resync)
    assert pool.schedule_tool_resync(conn)
    await await_signal(started, "worker started")
    closing = asyncio.create_task(pool.aclose())
    try:
        for _ in range(8):
            await asyncio.sleep(0)
        assert not closing.done(), "Pool disposed a still-running resync executor"
        disconnected.assert_not_called()
    finally:
        release.set()
        await closing
    assert finished.is_set()
    disconnected.assert_called_once()
    assert published == []


@pytest.mark.asyncio
async def test_actual_reconnect_schedules_on_connection_pool(pool_connection, monkeypatch):
    pool, conn = pool_connection
    started = asyncio.Event()
    conn._needs_tool_resync = True

    async def resync(_):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(uc, "_resync_tools_after_reconnect", resync)
    monkeypatch.setattr(uc, "_send_command_with_retry", lambda *_, **__: ({"ok": True}, conn))
    assert await uc.async_send_command_with_retry("ping", {}, instance_id=conn.instance_id) == {
        "ok": True
    }
    await await_signal(started, "scheduled resync")
    await pool.aclose()


@pytest.mark.asyncio
async def test_shutdown_releases_global_pool_for_next_server_generation(monkeypatch):
    first = uc.UnityConnectionPool()
    monkeypatch.setattr(uc, "_unity_connection_pool", first)
    await first.aclose()
    second = uc.get_unity_connection_pool()
    try:
        assert second is not first
        assert second._retired is False
    finally:
        await second.aclose()


@pytest.mark.asyncio
async def test_cancelled_close_still_drains_real_resync_worker(pool_connection, monkeypatch):
    pool, conn = pool_connection
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    disconnected = Mock()
    monkeypatch.setattr(conn, "disconnect", disconnected)

    def send(*_, **__):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(watchdog_timeout())
        return {"success": True}, conn

    async def resync(instance_id):
        await uc.async_send_command_with_retry("get_tool_states", {}, instance_id=instance_id)

    monkeypatch.setattr(uc, "_send_command_with_retry", send)
    monkeypatch.setattr(uc, "_resync_tools_after_reconnect", resync)
    pool.schedule_tool_resync(conn)
    await await_signal(started, "worker started")
    closing = asyncio.create_task(pool.aclose())
    try:
        for _ in range(8):
            await asyncio.sleep(0)
        closing.cancel()
        for _ in range(8):
            await asyncio.sleep(0)
        assert not closing.done(), "Cancellation abandoned physical resync work"
        assert pool._resync_workers, "Cancellation released a running executor owner"
        disconnected.assert_not_called()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
    disconnected.assert_called_once()


@pytest.mark.asyncio
async def test_retired_pool_cannot_publish_catalog(pool_connection, monkeypatch):
    pool, conn = pool_connection
    monkeypatch.setattr(uc, "_unity_connection_pool", pool)
    conn.sock = Mock()
    conn.session_generation = "old"
    published = []
    # Retirement happens before the first cleanup checkpoint.
    closing = asyncio.create_task(pool.aclose())
    await asyncio.sleep(0)
    try:
        with pytest.raises(ValueError, match="ownership"):
            uc._commit_stdio_catalog(conn, "old", lambda: published.append("stale"))
        assert published == []
    finally:
        await closing


@pytest.mark.asyncio
async def test_retirement_seals_stale_connection_without_closing_running_io(monkeypatch):
    pool = uc.UnityConnectionPool()
    conn = uc.UnityConnection(port=6400, instance_id="Main@deadbeef")
    pool._connections[conn.instance_id] = conn
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    disconnected = Mock()
    attempted_connections = Mock(side_effect=AssertionError("Retired owner tried to reconnect"))
    monkeypatch.setattr(conn, "disconnect", disconnected)
    monkeypatch.setattr(uc.socket, "create_connection", attempted_connections)

    def send(*_, **__):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(watchdog_timeout())
        assert conn.connect() is False
        return {"success": True}, conn

    async def resync(instance_id):
        await uc.async_send_command_with_retry("get_tool_states", {}, instance_id=instance_id)

    monkeypatch.setattr(pool, "get_connection", lambda _: conn)
    monkeypatch.setattr(uc, "_send_command_with_retry", send)
    monkeypatch.setattr(uc, "_resync_tools_after_reconnect", resync)
    pool.schedule_tool_resync(conn)
    await await_signal(started, "worker started")
    closing = asyncio.create_task(pool.aclose())
    try:
        for _ in range(8):
            await asyncio.sleep(0)
        assert conn._retired, "Stale connection can reconnect after pool retirement"
        disconnected.assert_not_called()
    finally:
        release.set()
        await closing
    attempted_connections.assert_not_called()
    disconnected.assert_called_once()


@pytest.mark.asyncio
async def test_queued_resync_selection_is_drained_without_dispatch(pool_connection, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    pool, conn = pool_connection
    loop = asyncio.get_running_loop()
    blocked = asyncio.Event()
    release = threading.Event()
    send = Mock(side_effect=AssertionError("Retired resync dispatched a command"))
    real_submit = uc.submit_owned_executor

    def occupy_worker():
        loop.call_soon_threadsafe(blocked.set)
        assert release.wait(watchdog_timeout())

    async def resync(instance_id):
        await uc.async_send_command_with_retry("get_tool_states", {}, instance_id=instance_id)

    with ThreadPoolExecutor(max_workers=1) as executor:
        occupying = loop.run_in_executor(executor, occupy_worker)
        await await_signal(blocked, "executor occupied")
        monkeypatch.setattr(
            uc,
            "submit_owned_executor",
            lambda current_loop, _, fn, *args: real_submit(current_loop, executor, fn, *args),
        )
        monkeypatch.setattr(uc, "_send_command_with_retry", send)
        monkeypatch.setattr(uc, "_resync_tools_after_reconnect", resync)
        pool.schedule_tool_resync(conn)
        for _ in range(8):
            await asyncio.sleep(0)
        closing = asyncio.create_task(pool.aclose())
        try:
            for _ in range(8):
                await asyncio.sleep(0)
            assert not closing.done(), "Queued executor ownership was abandoned"
        finally:
            release.set()
            await occupying
            await closing
    send.assert_not_called()


@pytest.mark.asyncio
async def test_initial_connection_cannot_publish_after_retirement(monkeypatch):
    pool = uc.UnityConnectionPool()
    target = SimpleNamespace(id="Main@deadbeef", port=6400)
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    disposed = []
    monkeypatch.setattr(pool, "resolve_instance", lambda _: target)

    def connect(connection):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(watchdog_timeout())
        return True

    def disconnect(connection):
        assert release.is_set(), "Socket owner was disposed before connect completed"
        disposed.append(connection)

    monkeypatch.setattr(uc.UnityConnection, "connect", connect)
    monkeypatch.setattr(uc.UnityConnection, "disconnect", disconnect)
    selecting = loop.run_in_executor(None, pool.get_connection, target.id)
    await await_signal(started, "initial connection started")
    closing = asyncio.create_task(pool.aclose())
    try:
        for _ in range(8):
            await asyncio.sleep(0)
        assert not closing.done()
    finally:
        release.set()
        with pytest.raises(ConnectionError, match="retired"):
            await selecting
        await closing
    assert len(disposed) == 1
    assert pool._connections == {}
