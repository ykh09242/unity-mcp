"""Explicit stdio pool teardown releases discovery generation metadata."""

from tests.timing import watchdog_timeout

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock

import pytest

from models.models import UnityInstanceInfo
import transport.legacy.unity_connection as connections


def instance():
    return UnityInstanceInfo(
        id="Owned@deadbeef",
        name="Owned",
        path="/Owned/Assets",
        hash="deadbeef",
        port=6400,
        status="running",
    )


def test_explicit_pool_teardown_releases_metadata_and_forces_next_discovery(monkeypatch):
    pool = connections.UnityConnectionPool()
    observed = instance()
    snapshot = {observed.id: observed}
    pool._known_instances = snapshot
    pool._target_refreshes = {observed.id: connections.time.time()}
    pool._last_full_scan = connections.time.time()
    connection = Mock()
    pool._connections = {observed.id: connection}
    discover = Mock(return_value=[])
    monkeypatch.setattr(connections.PortDiscovery, "discover_all_unity_instances", discover)

    pool.disconnect_all()

    connection.disconnect.assert_called_once()
    assert pool._connections == {}
    assert pool._known_instances == {}
    assert pool._target_refreshes == {}
    assert pool._last_full_scan is None
    assert snapshot == {observed.id: observed}, "Existing resource readers retain their snapshot"
    assert pool.discover_all_instances() == []
    discover.assert_called_once()


def test_pool_teardown_waits_for_existing_discovery_before_clearing_metadata(monkeypatch):
    pool = connections.UnityConnectionPool()
    scanning, release_scan, closing = Event(), Event(), Event()

    def discover():
        scanning.set()
        assert release_scan.wait(watchdog_timeout())
        return [instance()]

    def close():
        closing.set()
        pool.disconnect_all()

    monkeypatch.setattr(connections.PortDiscovery, "discover_all_unity_instances", discover)
    with ThreadPoolExecutor(max_workers=2) as executor:
        scan = executor.submit(pool.discover_all_instances)
        assert scanning.wait(watchdog_timeout())
        teardown = executor.submit(close)
        try:
            assert closing.wait(watchdog_timeout())
            release_scan.set()
            scan.result(timeout=watchdog_timeout())
            teardown.result(timeout=watchdog_timeout())
        finally:
            release_scan.set()
    assert pool._known_instances == {}
    assert pool._last_full_scan is None


@pytest.fixture
def populated_pool(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(connections.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(connections.UnityConnection, "connect", lambda self: True)
    pool = connections.UnityConnectionPool()
    observed = instance()
    monkeypatch.setattr(pool, "resolve_instance", lambda _: observed)
    connection = pool.get_connection(observed.id)
    socket = Mock()
    connection.sock = socket
    snapshot = []
    monkeypatch.setattr(
        connections.PortDiscovery, "discover_all_unity_instances", lambda: list(snapshot)
    )
    yield pool, connection, socket, clock, snapshot
    pool.disconnect_all()


def test_missing_idle_connection_is_reclaimed_after_grace(populated_pool):
    pool, connection, socket, clock, _ = populated_pool
    assert pool.discover_all_instances(force_refresh=True) == []
    assert len(pool._connections) == 1
    socket.close.assert_not_called()
    clock[0] += 61
    pool.discover_all_instances(force_refresh=True)
    assert pool._connections == {}
    socket.close.assert_called_once()
    assert connection.sock is None
    with pytest.raises(ConnectionError, match="retired"):
        with connection._command_lock(None):
            pytest.fail("A retired connection must not reopen for a stale caller")


def test_transient_discovery_omission_keeps_the_original_connection(populated_pool):
    pool, connection, socket, clock, snapshot = populated_pool
    pool.discover_all_instances(force_refresh=True)
    clock[0] += 61
    snapshot.append(instance())
    pool.discover_all_instances(force_refresh=True)
    assert pool.get_connection(instance().id) is connection
    socket.close.assert_not_called()


def test_retirement_never_interrupts_an_in_flight_command(populated_pool):
    pool, connection, socket, clock, _ = populated_pool
    entered, release = Event(), Event()

    def command():
        with connection._command_lock(None):
            entered.set()
            assert release.wait(watchdog_timeout())

    pool.discover_all_instances(force_refresh=True)
    clock[0] += 61
    with ThreadPoolExecutor(max_workers=1) as executor:
        running = executor.submit(command)
        assert entered.wait(watchdog_timeout())
        try:
            pool.discover_all_instances(force_refresh=True)
            assert pool._connections[instance().id] is connection
            socket.close.assert_not_called()
        finally:
            release.set()
        running.result(timeout=watchdog_timeout())
    pool.discover_all_instances(force_refresh=True)
    assert pool._connections == {}
    socket.close.assert_called_once()


def test_reentrant_discovery_cannot_retire_its_own_command(populated_pool):
    pool, connection, socket, clock, _ = populated_pool
    pool.discover_all_instances(force_refresh=True)
    clock[0] += 61
    with connection._command_lock(None):
        pool.discover_all_instances(force_refresh=True)
        assert pool._connections[instance().id] is connection
        socket.close.assert_not_called()
    pool.discover_all_instances(force_refresh=True)
    assert pool._connections == {}


def test_pool_capacity_does_not_evict_another_live_target(populated_pool, monkeypatch):
    pool, connection, socket, _, snapshot = populated_pool
    snapshot.append(instance())
    monkeypatch.setattr(pool, "MAX_CONNECTIONS", 1, raising=False)
    second = instance().model_copy(update={"id": "Second@12345678", "port": 6401})
    monkeypatch.setattr(pool, "resolve_instance", lambda _: second)
    with pytest.raises(ConnectionError, match="capacity"):
        pool.get_connection(second.id)
    assert pool._connections == {instance().id: connection}
    socket.close.assert_not_called()


@pytest.mark.asyncio
async def test_queued_async_admission_prevents_retirement(populated_pool):
    import asyncio  # noqa: ANYIO_OK -- exercise the existing loop-scoped admission gate.

    pool, connection, socket, clock, _ = populated_pool
    gate = connection._async_admission(asyncio.get_running_loop())
    pool.discover_all_instances(force_refresh=True)
    clock[0] += 61
    await gate.acquire()
    try:
        pool.discover_all_instances(force_refresh=True)
        assert pool._connections[instance().id] is connection
        socket.close.assert_not_called()
    finally:
        gate.release()
    pool.discover_all_instances(force_refresh=True)
    assert pool._connections == {}


@pytest.mark.asyncio
async def test_admission_handoff_keeps_waiter_owned_before_it_resumes(populated_pool):
    import asyncio  # noqa: ANYIO_OK -- schedule the existing admission boundary precisely.

    pool, connection, socket, clock, _ = populated_pool
    gate = connection._async_admission(asyncio.get_running_loop())
    pool.discover_all_instances(force_refresh=True)
    clock[0] += 61
    await gate.acquire()
    waiter = asyncio.create_task(gate.acquire())
    await asyncio.sleep(0)
    gate.release()
    try:
        # The previous holder released, but the awakened waiter has not resumed.
        pool.discover_all_instances(force_refresh=True)
        assert pool._connections.get(instance().id) is connection
        socket.close.assert_not_called()
    finally:
        await waiter
        gate.release()
    pool.discover_all_instances(force_refresh=True)
    assert pool._connections == {}


@pytest.mark.asyncio
async def test_cancelled_admission_waiter_does_not_keep_connection_alive(populated_pool):
    import asyncio  # noqa: ANYIO_OK -- cancel the existing loop-scoped admission.

    pool, connection, _, clock, _ = populated_pool
    gate = connection._async_admission(asyncio.get_running_loop())
    pool.discover_all_instances(force_refresh=True)
    clock[0] += 61
    await gate.acquire()
    waiter = asyncio.create_task(gate.acquire())
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    gate.release()
    pool.discover_all_instances(force_refresh=True)
    assert pool._connections == {}
