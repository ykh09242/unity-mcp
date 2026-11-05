"""Pool selection owns one target without holding unrelated Editors hostage."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event, Thread
import time
from unittest.mock import Mock

import pytest

from core.config import config
from models.models import UnityInstanceInfo
from tests.timing import watchdog_timeout
import transport.legacy.unity_connection as uc
from services.registry import operation_invocation


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


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_public_abandoned_selection_never_connects_after_predecessor(
    selection_pool, monkeypatch, cancel
):
    # Given a public call waiting behind a genuine target selection owner.
    pool, _ = selection_pool
    monkeypatch.setattr(config, "transport_mode", "stdio")
    entered, finished = asyncio.Event(), Event()
    loop = asyncio.get_running_loop()
    predecessor = Event()
    pool._selection_owners["Main@deadbeef"] = predecessor
    connected = []
    handlers = []
    deadlines = []
    original = pool.get_connection

    class Context:
        async def get_state(self, key, default=None):
            return "Main@deadbeef" if key == "unity_instance" else default

    def select(identity):
        if identity != "Main@deadbeef":
            return original(identity)
        selection = uc._connection_selection.get()
        deadlines.append(selection.deadline if selection is not None else None)
        loop.call_soon_threadsafe(entered.set)
        try:
            return original(identity)
        finally:
            finished.set()

    def connect(connection, **kwargs):
        connected.append(connection.instance_id)
        connection.sock = Mock()
        return True

    async def handler(ctx):
        handlers.append(True)

    expires_at = time.monotonic() + (30 if cancel else 1)
    monkeypatch.setattr(operation_invocation, "_deadline", lambda *a, **kw: expires_at)
    monkeypatch.setattr(pool, "get_connection", select)
    monkeypatch.setattr(uc.UnityConnection, "connect", connect)
    request = asyncio.create_task(
        operation_invocation.wrap_tool(handler, "manage_scene", "manage_scene")(Context())
    )
    settled_before_release = False
    try:
        await asyncio.wait_for(entered.wait(), watchdog_timeout(5))
        # When the public caller abandons selection, another target still progresses.
        other = await asyncio.wait_for(
            uc._select_connection("Other@cafebabe", None, None), watchdog_timeout(5)
        )
        assert other.instance_id == "Other@cafebabe"
        if cancel:
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
        else:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(request, watchdog_timeout(5))
        settled_before_release = await asyncio.to_thread(finished.wait, watchdog_timeout(1))
        assert pool._selection_owners["Main@deadbeef"] is predecessor
    finally:
        with pool._pool_lock:
            pool._selection_owners.pop("Main@deadbeef", None)
            predecessor.set()
        await asyncio.to_thread(finished.wait, watchdog_timeout(5))
        if not request.done():
            request.cancel()
        await asyncio.gather(request, return_exceptions=True)

    # Then release cannot connect or publish the abandoned target, or run its handler.
    assert connected == ["Other@cafebabe"]
    assert "Main@deadbeef" not in pool._connections
    assert handlers == []
    assert settled_before_release
    assert deadlines == [expires_at]


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
@pytest.mark.parametrize("public_entry", [False, True])
async def test_cancelled_executor_queued_selection_never_connects(
    selection_pool, monkeypatch, public_entry
):
    pool, _ = selection_pool
    monkeypatch.setattr(config, "transport_mode", "stdio")
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
        if public_entry:

            class Context:
                async def get_state(self, key, default=None):
                    return "Main@deadbeef" if key == "unity_instance" else default

            async def handler(ctx):
                pytest.fail("Abandoned public selection ran its handler")

            call = operation_invocation.wrap_tool(handler, "manage_scene", "manage_scene")(
                Context()
            )
        else:
            call = uc.async_send_command_with_retry(
                "manage_scene",
                {"action": "get_active"},
                instance_id="Main@deadbeef",
            )
        request = asyncio.create_task(call)
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


@pytest.mark.asyncio
@pytest.mark.parametrize("selector", ["Main@deadbeef", "Main", "deadbeef"])
async def test_duplicate_async_selection_keeps_executor_available_for_other_target(
    selection_pool, monkeypatch, selector
):
    pool, targets = selection_pool
    pool._known_instances = targets.copy()
    monkeypatch.setattr(
        pool,
        "resolve_instance",
        lambda identity: pool._resolve_instance_id(identity, list(targets.values())),
    )
    loop = asyncio.get_running_loop()
    entered, release = asyncio.Event(), Event()
    submissions = []
    real_submit = uc.submit_owned_executor

    def connect(connection, **kwargs):
        if connection.instance_id == "Main@deadbeef":
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(watchdog_timeout(5))
        connection.sock = Mock()
        return True

    monkeypatch.setattr(uc.UnityConnection, "connect", connect)
    with ThreadPoolExecutor(max_workers=4) as executor:

        def submit(current_loop, _, function, *args):
            submissions.append(args[-1])
            return real_submit(current_loop, executor, function, *args)

        monkeypatch.setattr(uc, "submit_owned_executor", submit)
        requests = [
            asyncio.create_task(
                uc._select_connection("Main@deadbeef" if index == 0 else selector, None, pool)
            )
            for index in range(64)
        ]
        await asyncio.wait_for(entered.wait(), watchdog_timeout(5))
        other = asyncio.create_task(uc._select_connection("Other@cafebabe", None, pool))
        try:
            selected = await asyncio.wait_for(asyncio.shield(other), watchdog_timeout(0.5))
            assert selected.instance_id == "Other@cafebabe"
            for request in requests[1:]:
                request.cancel()
            await asyncio.gather(*requests[1:], return_exceptions=True)
            assert not requests[0].done(), "Cancelling waiters cancelled the shared owner"
            expected = ["Main@deadbeef"]
            if selector != "Main@deadbeef":
                expected.append(selector)
            assert submissions == [*expected, "Other@cafebabe"]
        finally:
            release.set()
            await asyncio.gather(*requests, other, return_exceptions=True)
    assert (await requests[0]).instance_id == "Main@deadbeef"


@pytest.mark.asyncio
async def test_whitespace_equivalent_exact_selectors_leave_worker_for_other_target(
    selection_pool, monkeypatch
):
    pool, targets = selection_pool
    monkeypatch.setattr(
        pool,
        "resolve_instance",
        lambda identity: pool._resolve_instance_id(identity, list(targets.values())),
    )
    loop = asyncio.get_running_loop()
    entered, release = asyncio.Event(), Event()
    real_submit = uc.submit_owned_executor
    submitted = []
    selectors = [" Main@deadbeef", "Main@deadbeef", "Main@deadbeef ", " Main@deadbeef "]

    def connect(connection, **kwargs):
        if connection.instance_id == "Main@deadbeef":
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(watchdog_timeout(5))
        connection.sock = Mock()
        return True

    monkeypatch.setattr(uc.UnityConnection, "connect", connect)
    with ThreadPoolExecutor(max_workers=4) as executor:

        def submit(current_loop, _, function, *args):
            submitted.append(args[-1])
            return real_submit(current_loop, executor, function, *args)

        monkeypatch.setattr(uc, "submit_owned_executor", submit)
        requests = [
            asyncio.create_task(uc._select_connection(selector, None, pool))
            for selector in selectors
        ]
        await asyncio.wait_for(entered.wait(), watchdog_timeout(5))
        other = asyncio.create_task(uc._select_connection("Other@cafebabe", None, pool))
        try:
            selected = await asyncio.wait_for(asyncio.shield(other), watchdog_timeout(0.5))
            assert selected.instance_id == "Other@cafebabe"
            assert submitted == [selectors[0], "Other@cafebabe"]
        finally:
            release.set()
            await asyncio.gather(*requests, other, return_exceptions=True)
    selected = await asyncio.gather(*requests)
    assert all(connection is selected[0] for connection in selected)


@pytest.mark.asyncio
async def test_stale_cached_alias_cannot_share_exact_id_validation(selection_pool, monkeypatch):
    pool, targets = selection_pool
    exact = "Main@deadbeef"
    pool._known_instances = {exact: targets[exact]}
    loop = asyncio.get_running_loop()
    entered, release = asyncio.Event(), Event()
    checked = []

    def resolve(identity):
        checked.append(identity)
        if identity == "Main":
            # Fresh discovery now contains two Editors named Main, while the
            # previous cached inventory named only the exact target below.
            raise ConnectionError("Multiple Unity instances match 'Main'")
        return targets[identity]

    def connect(connection, **kwargs):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(watchdog_timeout(5))
        connection.sock = Mock()
        return True

    monkeypatch.setattr(pool, "resolve_instance", resolve)
    monkeypatch.setattr(uc.UnityConnection, "connect", connect)
    first = asyncio.create_task(uc._select_connection(exact, None, pool))
    await asyncio.wait_for(entered.wait(), watchdog_timeout(5))
    alias = asyncio.create_task(uc._select_connection("Main", None, pool))
    await asyncio.sleep(0)
    release.set()
    assert (await first).instance_id == exact
    with pytest.raises(ConnectionError, match="Multiple Unity instances"):
        await alias
    assert checked == [exact, "Main"]


@pytest.mark.asyncio
async def test_shared_selection_waiters_keep_individual_deadlines(selection_pool, monkeypatch):
    pool, _ = selection_pool
    loop = asyncio.get_running_loop()
    entered, release = asyncio.Event(), Event()
    calls = []

    def connect(connection, **kwargs):
        calls.append(connection)
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(watchdog_timeout(5))
        uc._connection_selection.get().check()
        connection.sock = Mock()
        return True

    monkeypatch.setattr(uc.UnityConnection, "connect", connect)
    deadline = time.monotonic() + 0.15
    first = asyncio.create_task(uc._select_connection("Main@deadbeef", deadline, pool))
    await asyncio.wait_for(entered.wait(), watchdog_timeout(5))
    following = asyncio.create_task(
        uc._select_connection("Main@deadbeef", time.monotonic() + 5, pool)
    )
    try:
        with pytest.raises(TimeoutError):
            await first
        assert not following.done()
    finally:
        release.set()
    assert await following is calls[0]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_first_selection_waiter_can_cancel_without_abandoning_following_waiter(
    selection_pool, monkeypatch
):
    pool, _ = selection_pool
    loop = asyncio.get_running_loop()
    entered, release = asyncio.Event(), Event()
    connected = []

    def connect(connection, **kwargs):
        connected.append(connection)
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(watchdog_timeout(5))
        uc._connection_selection.get().check()
        connection.sock = Mock()
        return True

    monkeypatch.setattr(uc.UnityConnection, "connect", connect)
    first = asyncio.create_task(uc._select_connection("Main@deadbeef", None, pool))
    await asyncio.wait_for(entered.wait(), watchdog_timeout(5))
    following = asyncio.create_task(uc._select_connection("Main@deadbeef", None, pool))
    await asyncio.sleep(0)
    try:
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not following.done()
    finally:
        release.set()
    assert await following is connected[0]
    assert len(connected) == 1


@pytest.mark.asyncio
async def test_longer_waiter_retries_expired_physical_connect_budget(selection_pool, monkeypatch):
    pool, _ = selection_pool
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    deadlines = []

    def connect(connection, **kwargs):
        deadline = kwargs["deadline"]
        deadlines.append(deadline)
        if len(deadlines) == 1:
            loop.call_soon_threadsafe(entered.set)
            # A blocking connect/handshake already captured the first caller's
            # deadline before the later waiter extended the shared selection.
            Event().wait(max(0.0, deadline - time.monotonic()) + 0.02)
            return False
        connection.sock = Mock()
        return True

    monkeypatch.setattr(uc.UnityConnection, "connect", connect)
    first = asyncio.create_task(
        uc._select_connection("Main@deadbeef", time.monotonic() + 0.15, pool)
    )
    await asyncio.wait_for(entered.wait(), watchdog_timeout(5))
    following = asyncio.create_task(
        uc._select_connection("Main@deadbeef", time.monotonic() + 5, pool)
    )
    with pytest.raises(TimeoutError):
        await first
    selected = await following
    assert selected.instance_id == "Main@deadbeef"
    assert len(deadlines) == 2 and deadlines[1] > deadlines[0]


@pytest.mark.asyncio
async def test_selection_capacity_rejects_excess_before_executor_submission(
    selection_pool, monkeypatch
):
    pool, _ = selection_pool
    pool.MAX_CONNECTIONS = 1
    loop = asyncio.get_running_loop()
    entered, release = asyncio.Event(), Event()
    connected = []

    def connect(connection, **kwargs):
        connected.append(connection)
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(watchdog_timeout(5))
        connection.sock = Mock()
        return True

    monkeypatch.setattr(uc.UnityConnection, "connect", connect)
    first = asyncio.create_task(uc._select_connection("Main@deadbeef", None, pool))
    await asyncio.wait_for(entered.wait(), watchdog_timeout(5))
    try:
        with pytest.raises(ConnectionError, match="selection capacity"):
            await uc._select_connection("Other@cafebabe", None, pool)
    finally:
        release.set()
        await first
    assert len(connected) == 1


@pytest.mark.asyncio
async def test_executor_queued_selection_cannot_cross_pool_reset(selection_pool, monkeypatch):
    pool, _ = selection_pool
    loop = asyncio.get_running_loop()
    entered, submitted, release = asyncio.Event(), asyncio.Event(), Event()
    real_submit = uc.submit_owned_executor
    connect = Mock(side_effect=AssertionError("Old selection connected after pool reset"))
    monkeypatch.setattr(uc.UnityConnection, "connect", connect)

    def occupy():
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(watchdog_timeout(5))

    with ThreadPoolExecutor(max_workers=1) as executor:
        occupied = loop.run_in_executor(executor, occupy)
        await asyncio.wait_for(entered.wait(), watchdog_timeout(5))

        def submit(current_loop, _, function, *args):
            future = real_submit(current_loop, executor, function, *args)
            submitted.set()
            return future

        monkeypatch.setattr(uc, "submit_owned_executor", submit)
        request = asyncio.create_task(uc._select_connection("Main@deadbeef", None, pool))
        await asyncio.wait_for(submitted.wait(), watchdog_timeout(5))
        pool.disconnect_all()
        release.set()
        await occupied
        with pytest.raises(ConnectionError, match="generation.*retired"):
            await request
    connect.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_close", [False, True])
async def test_pool_close_on_another_loop_drains_physical_selection(
    selection_pool, monkeypatch, cancel_close
):
    pool, _ = selection_pool
    owner_loop = asyncio.new_event_loop()
    loop_thread = Thread(target=owner_loop.run_forever)
    entered, release = Event(), Event()
    retained_socket = Mock()
    cached = uc.UnityConnection(port=6401, instance_id="Other@cafebabe", sock=retained_socket)
    pool._connections[cached.instance_id] = cached

    def connect(connection, **kwargs):
        entered.set()
        assert release.wait(watchdog_timeout(5))
        connection.sock = Mock()
        return True

    monkeypatch.setattr(uc.UnityConnection, "connect", connect)
    loop_thread.start()
    selecting = asyncio.run_coroutine_threadsafe(
        uc._select_connection("Main@deadbeef", None, pool), owner_loop
    )
    closing = None
    second_close = None
    try:
        assert await asyncio.to_thread(entered.wait, watchdog_timeout(5))
        closing = asyncio.create_task(pool.aclose())
        for _ in range(8):
            await asyncio.sleep(0)
        assert not closing.done(), "Foreign-loop close abandoned a live physical selection"
        retained_socket.close.assert_not_called()
        second_close = asyncio.run_coroutine_threadsafe(pool.aclose(), owner_loop)
        if cancel_close:
            closing.cancel()
            for _ in range(8):
                await asyncio.sleep(0)
            assert not closing.done(), "Close cancellation abandoned the foreign-loop worker"
        release.set()
        if cancel_close:
            with pytest.raises(asyncio.CancelledError):
                await closing
        else:
            await closing
        await asyncio.wrap_future(second_close)
        with pytest.raises(ConnectionError, match="cancelled|retired"):
            await asyncio.wrap_future(selecting)
        assert pool._connections == {}
        retained_socket.close.assert_called_once()
    finally:
        release.set()
        await asyncio.gather(asyncio.wrap_future(selecting), return_exceptions=True)
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        if second_close is not None:
            await asyncio.gather(asyncio.wrap_future(second_close), return_exceptions=True)
        owner_loop.call_soon_threadsafe(owner_loop.stop)
        await asyncio.to_thread(loop_thread.join, watchdog_timeout(5))
        assert not loop_thread.is_alive()
        owner_loop.close()


@pytest.mark.asyncio
async def test_cancelled_selection_fanout_retains_bounded_submitted_work(
    selection_pool, monkeypatch
):
    pool, _ = selection_pool
    loop = asyncio.get_running_loop()
    entered, release = asyncio.Event(), Event()
    submitted = []
    real_submit = uc.submit_owned_executor

    def select(identity):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(watchdog_timeout(5))
        uc._connection_selection.get().check()
        pytest.fail("Abandoned selection reached connection creation")

    monkeypatch.setattr(pool, "get_connection", select)
    with ThreadPoolExecutor(max_workers=1) as executor:

        def submit(current_loop, _, function, *args):
            future = real_submit(current_loop, executor, function, *args)
            submitted.append(future)
            return future

        monkeypatch.setattr(uc, "submit_owned_executor", submit)
        requests = [
            asyncio.create_task(uc._select_connection(f"Editor-{index}@deadbeef", None, pool))
            for index in range(64)
        ]
        await asyncio.wait_for(entered.wait(), watchdog_timeout(5))
        try:
            for request in requests:
                request.cancel()
            await asyncio.gather(*requests, return_exceptions=True)
            assert len(submitted) <= 4, "Cancellation retained unbounded executor queue entries"
            assert pool._resync_workers, "Running selection lost its physical owner"
            closing = asyncio.create_task(pool.aclose())
            await asyncio.sleep(0)
            assert not closing.done(), "Pool retirement discarded a running selection"
        finally:
            release.set()
            await asyncio.gather(*submitted, return_exceptions=True)
            if "closing" in locals():
                await closing
    assert pool._resync_workers == set()
