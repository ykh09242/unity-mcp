"""Pool selection owns one target without holding unrelated Editors hostage."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event
import time
from unittest.mock import Mock

import pytest

from core.config import config
from models.models import UnityInstanceInfo
from tests.timing import watchdog_timeout
import transport.legacy.unity_connection as uc


@pytest.fixture
def selection_pool(monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    pool = uc.UnityConnectionPool()
    targets = {}
    for index, identity in enumerate(("Main@deadbeef", "Other@cafebabe")):
        name, hash_value = identity.split("@")
        targets[identity] = UnityInstanceInfo(
            id=identity,
            name=name,
            hash=hash_value,
            path=f"/{name}/Assets",
            port=6400 + index,
            status="running",
        )
    monkeypatch.setattr(pool, "resolve_instance", lambda identity: targets[identity])
    monkeypatch.setattr(uc, "get_unity_connection_pool", lambda: pool)
    monkeypatch.setattr(uc.UnityConnection, "_ensure_live_connection", lambda self: None)
    monkeypatch.setattr(uc.UnityConnection, "send_command", lambda *a, **kw: {"success": True})
    yield pool, targets
    pool.disconnect_all()


@pytest.mark.parametrize("mode", ["initial", "reconnect", "port_change"])
def test_slow_target_selection_does_not_block_cached_other(selection_pool, monkeypatch, mode):
    pool, targets = selection_pool
    entered, release, replacing = Event(), Event(), Event()
    other = uc.UnityConnection(port=6401, instance_id="Other@cafebabe", sock=Mock())
    pool._connections[other.instance_id] = other
    main = uc.UnityConnection(port=6400, instance_id="Main@deadbeef")
    if mode != "initial":
        pool._connections[main.instance_id] = main
    if mode == "port_change":
        targets[main.instance_id] = targets[main.instance_id].model_copy(update={"port": 6402})

    def connect(connection, **kwargs):
        if mode != "port_change":
            entered.set()
            assert release.wait(watchdog_timeout(5))
        connection.sock = Mock()
        return True

    def command():
        with main._command_lock(None):
            entered.set()
            assert release.wait(watchdog_timeout(5))

    def select_main():
        replacing.set()
        return pool.get_connection(main.instance_id)

    monkeypatch.setattr(uc.UnityConnection, "connect", connect)
    with ThreadPoolExecutor(max_workers=3) as executor:
        running = executor.submit(command) if mode == "port_change" else None
        if running is not None:
            assert entered.wait(watchdog_timeout(5))
        selecting = executor.submit(select_main)
        try:
            assert entered.wait(watchdog_timeout(5))
            assert replacing.wait(watchdog_timeout(5))
            selected_other = executor.submit(pool.get_connection, other.instance_id)
            assert selected_other.result(timeout=watchdog_timeout(1)) is other
        finally:
            release.set()
        selected = selecting.result(timeout=watchdog_timeout(5))
        assert selected.port == targets[main.instance_id].port
        if running is not None:
            running.result(timeout=watchdog_timeout(5))


def test_same_target_has_one_connect_owner(selection_pool, monkeypatch):
    pool, _ = selection_pool
    entered, release = Event(), Event()
    created = []

    def connect(connection, **kwargs):
        created.append(connection)
        entered.set()
        assert release.wait(watchdog_timeout(5))
        connection.sock = Mock()
        return True

    monkeypatch.setattr(uc.UnityConnection, "connect", connect)
    with ThreadPoolExecutor(max_workers=3) as executor:
        first = executor.submit(pool.get_connection, "Main@deadbeef")
        assert entered.wait(watchdog_timeout(5))
        waiting = [executor.submit(pool.get_connection, "Main@deadbeef") for _ in range(2)]
        release.set()
        selected = first.result(timeout=watchdog_timeout(5))
        assert all(future.result(timeout=watchdog_timeout(5)) is selected for future in waiting)
    assert created == [selected]


def test_pending_connect_reserves_pool_capacity(selection_pool, monkeypatch):
    pool, targets = selection_pool
    pool.MAX_CONNECTIONS = 1
    entered, release = Event(), Event()

    def connect(connection, **kwargs):
        entered.set()
        assert release.wait(watchdog_timeout(5))
        connection.sock = Mock()
        return True

    monkeypatch.setattr(uc.UnityConnection, "connect", connect)
    monkeypatch.setattr(
        uc.PortDiscovery, "discover_all_unity_instances", lambda: list(targets.values())
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(pool.get_connection, "Main@deadbeef")
        assert entered.wait(watchdog_timeout(5))
        try:
            with pytest.raises(ConnectionError, match="capacity"):
                executor.submit(pool.get_connection, "Other@cafebabe").result(
                    timeout=watchdog_timeout(1)
                )
        finally:
            release.set()
        first.result(timeout=watchdog_timeout(5))


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_abandoned_same_target_waiter_keeps_connect_owner(
    selection_pool, monkeypatch, cancel
):
    pool, _ = selection_pool
    entered, release, waiter_finished = Event(), Event(), Event()
    waiter_entered = asyncio.Event()
    loop = asyncio.get_running_loop()
    created = []

    def connect(connection, **kwargs):
        created.append(connection)
        entered.set()
        assert release.wait(watchdog_timeout(5))
        connection.sock = Mock()
        return True

    monkeypatch.setattr(uc.UnityConnection, "connect", connect)
    first = loop.run_in_executor(None, pool.get_connection, "Main@deadbeef")
    assert await asyncio.to_thread(entered.wait, watchdog_timeout(5))
    original = pool.get_connection

    def select(identity):
        loop.call_soon_threadsafe(waiter_entered.set)
        try:
            return original(identity)
        finally:
            waiter_finished.set()

    monkeypatch.setattr(pool, "get_connection", select)
    waiter = asyncio.create_task(
        uc.async_send_command_with_retry(
            "manage_scene",
            {"action": "get_active"},
            instance_id="Main@deadbeef",
            _deadline=time.monotonic() + (30 if cancel else 0.15),
        )
    )
    await asyncio.wait_for(waiter_entered.wait(), watchdog_timeout(5))
    try:
        if cancel:
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
        else:
            result = await asyncio.wait_for(waiter, watchdog_timeout(5))
            assert result.data["dispatch"] == "not_dispatched"
        assert await asyncio.to_thread(waiter_finished.wait, watchdog_timeout(1))
        assert not first.done(), "The original connect still owns its worker"
        with pool._pool_lock:
            assert "Main@deadbeef" in pool._selection_owners
        following = loop.run_in_executor(None, original, "Main@deadbeef")
    finally:
        release.set()
    selected = await first
    assert await following is selected
    assert created == [selected]


@pytest.mark.asyncio
async def test_cancelled_executor_queued_selection_never_connects(selection_pool, monkeypatch):
    pool, _ = selection_pool
    entered, release, submitted = Event(), Event(), asyncio.Event()
    loop = asyncio.get_running_loop()
    real_submit = uc.submit_owned_executor
    connect = Mock(side_effect=AssertionError("Abandoned selector connected"))
    monkeypatch.setattr(uc.UnityConnection, "connect", connect)

    def occupy():
        entered.set()
        assert release.wait(watchdog_timeout(5))

    with ThreadPoolExecutor(max_workers=1) as executor:
        occupied = loop.run_in_executor(executor, occupy)
        assert await asyncio.to_thread(entered.wait, watchdog_timeout(5))
        workers = []

        def submit(current_loop, _, function, *args):
            worker = real_submit(current_loop, executor, function, *args)
            workers.append(worker)
            submitted.set()
            return worker

        monkeypatch.setattr(uc, "submit_owned_executor", submit)
        request = asyncio.create_task(
            uc.async_send_command_with_retry(
                "manage_scene",
                {"action": "get_active"},
                instance_id="Main@deadbeef",
            )
        )
        await asyncio.wait_for(submitted.wait(), watchdog_timeout(5))
        try:
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            assert not workers[0].done(), "Queued worker ownership must survive caller cancellation"
        finally:
            release.set()
            await occupied
            await asyncio.gather(*workers, return_exceptions=True)
    connect.assert_not_called()
    assert pool._connections == {}


@pytest.mark.asyncio
async def test_cancelled_reconnect_cleans_socket_before_publication(selection_pool, monkeypatch):
    pool, _ = selection_pool
    connection = uc.UnityConnection(port=6400, instance_id="Main@deadbeef")
    pool._connections[connection.instance_id] = connection
    connected, returning, finished = Event(), Event(), Event()
    sock = Mock()
    original = pool.get_connection
    publishing = asyncio.Event()
    loop = asyncio.get_running_loop()
    original_lock = uc._ConnectionSelection.lock

    @contextmanager
    def observe_lock(selection, lock):
        if lock is pool._pool_lock and connected.is_set():
            loop.call_soon_threadsafe(publishing.set)
        with original_lock(selection, lock):
            yield

    def connect(connection, **kwargs):
        connection.sock = sock
        connected.set()
        assert returning.wait(watchdog_timeout(5))
        return True

    def select(identity):
        try:
            return original(identity)
        finally:
            finished.set()

    monkeypatch.setattr(uc.UnityConnection, "connect", connect)
    monkeypatch.setattr(pool, "get_connection", select)
    monkeypatch.setattr(uc._ConnectionSelection, "lock", observe_lock)
    request = asyncio.create_task(
        uc.async_send_command_with_retry(
            "manage_scene",
            {"action": "get_active"},
            instance_id=connection.instance_id,
        )
    )
    assert await asyncio.to_thread(connected.wait, watchdog_timeout(5))
    assert pool._pool_lock.acquire(blocking=False)
    try:
        returning.set()
        # Observe publication waiting after the reconnect, rather than racing
        # cancellation against the connect callback's own completion.
        await asyncio.wait_for(publishing.wait(), watchdog_timeout(5))
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
    finally:
        pool._pool_lock.release()
        returning.set()
    assert await asyncio.to_thread(finished.wait, watchdog_timeout(5))
    assert connection.sock is None
    sock.close.assert_called_once()


def test_pool_reset_rejects_pending_connection_publication(selection_pool, monkeypatch):
    pool, _ = selection_pool
    entered, release, reset = Event(), Event(), Event()
    sockets = []

    def connect(connection, **kwargs):
        entered.set()
        assert release.wait(watchdog_timeout(5))
        connection.sock = Mock()
        sockets.append(connection.sock)
        return True

    def close():
        pool.disconnect_all()
        reset.set()

    monkeypatch.setattr(uc.UnityConnection, "connect", connect)
    with ThreadPoolExecutor(max_workers=2) as executor:
        selecting = executor.submit(pool.get_connection, "Main@deadbeef")
        assert entered.wait(watchdog_timeout(5))
        closing = executor.submit(close)
        try:
            assert reset.wait(watchdog_timeout(1)), "Reset must fence an unfinished selection"
        finally:
            release.set()
        closing.result(timeout=watchdog_timeout(5))
        with pytest.raises(ConnectionError, match="generation|retired"):
            selecting.result(timeout=watchdog_timeout(5))
    assert pool._connections == {}
    sockets[0].close.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_abandoned_selection_does_not_publish_or_dispatch(
    selection_pool, monkeypatch, cancel
):
    pool, _ = selection_pool
    entered, release, finished = Event(), Event(), Event()
    sockets, deadlines, dispatched = [], [], []

    def connect(connection, **kwargs):
        deadlines.append(kwargs.get("deadline"))
        entered.set()
        assert release.wait(watchdog_timeout(5))
        connection.sock = Mock()
        sockets.append(connection.sock)
        return True

    original = pool.get_connection

    def select(identity):
        try:
            return original(identity)
        finally:
            finished.set()

    monkeypatch.setattr(pool, "get_connection", select)
    monkeypatch.setattr(uc.UnityConnection, "connect", connect)
    monkeypatch.setattr(uc.UnityConnection, "send_command", lambda *a, **kw: dispatched.append(1))
    deadline = time.monotonic() + (30 if cancel else 0.15)
    request = asyncio.create_task(
        uc.async_send_command_with_retry(
            "manage_scene",
            {"action": "get_active"},
            instance_id="Main@deadbeef",
            _deadline=deadline,
        )
    )
    assert await asyncio.to_thread(entered.wait, watchdog_timeout(5))
    try:
        if cancel:
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
        else:
            result = await asyncio.wait_for(request, watchdog_timeout(5))
            assert result.data == {
                "reason": "command_deadline_exceeded",
                "dispatch": "not_dispatched",
            }
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, watchdog_timeout(5))
    assert deadlines == [deadline]
    assert pool._connections == {}
    assert dispatched == []
    sockets[0].close.assert_called_once()


@pytest.mark.parametrize("targeted", [False, True])
def test_discovery_ttl_ignores_backward_wall_clock(monkeypatch, targeted):
    wall, monotonic = [1000.0], [10.0]
    monkeypatch.setattr(uc.time, "time", lambda: wall[0])
    monkeypatch.setattr(uc.time, "monotonic", lambda: monotonic[0])
    monkeypatch.setattr(config, "http_remote_hosted", False)
    pool = uc.UnityConnectionPool()
    instance = UnityInstanceInfo(
        id="Main@deadbeef",
        name="Main",
        hash="deadbeef",
        path="/Main/Assets",
        port=6400,
        status="running",
    )
    discover = Mock(return_value=instance if targeted else [instance])
    monkeypatch.setattr(
        uc.PortDiscovery,
        "discover_unity_instance" if targeted else "discover_all_unity_instances",
        discover,
    )
    lookup = lambda: (
        pool.resolve_instance(instance.id) if targeted else pool.discover_all_instances()
    )
    lookup()
    wall[0] -= 600
    monotonic[0] += 4
    lookup()
    assert discover.call_count == 1
    monotonic[0] += 1
    lookup()
    assert discover.call_count == 2
