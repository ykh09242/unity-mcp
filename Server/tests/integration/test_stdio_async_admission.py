"""Async commands wait without occupying workers needed by other Editors."""

from tests.timing import watchdog_timeout

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import gc
import json
import socket
import struct
import threading
import time
import weakref
from types import SimpleNamespace

import pytest

from core.config import config
from models.models import UnityInstanceInfo
import transport.legacy.unity_connection as uc
from transport.legacy.stdio_auth import StdioAuthenticatedSession


class ResponseSocket:
    """Only socket I/O is fake; real framing, locks and deadlines execute."""

    def __init__(self):
        self.timeout = 30.0
        self.buffer = b""
        self.header_pending = True
        self.delay_pending = False
        self.release = threading.Event()
        self.sent = threading.Event()
        self.tags = []
        self.closed = False

    def gettimeout(self):
        return self.timeout

    def settimeout(self, value):
        self.timeout = value

    def setblocking(self, value):
        self.timeout = None if value else 0.0

    def sendall(self, payload):
        if self.header_pending:
            assert len(payload) == 8
            self.header_pending = False
            return
        self.header_pending = True
        tag = "ping" if payload == b"ping" else json.loads(payload)["params"]["tag"]
        self.tags.append(tag)
        response = json.dumps(
            {
                "status": "success",
                "result": {"message": "pong"}
                if payload == b"ping"
                else {"success": True, "tag": tag},
            }
        ).encode()
        self.buffer = struct.pack(">Q", len(response)) + response
        self.delay_pending = True
        self.sent.set()

    def recv(self, count, flags=0):
        if flags:
            raise BlockingIOError()
        if self.delay_pending:
            self.delay_pending = False
            if not self.release.wait(min(2.0, self.timeout)):
                raise socket.timeout("controlled response deadline")
        result, self.buffer = self.buffer[:count], self.buffer[count:]
        return result

    def close(self):
        self.closed = True


async def wait_event(event):
    ceiling = time.monotonic() + 2
    while not event.is_set():
        assert time.monotonic() < ceiling, "controlled worker failed to progress"
        await asyncio.sleep(0.001)


@pytest.fixture
def environment(monkeypatch):
    monkeypatch.setattr(config, "http_remote_hosted", False)
    monkeypatch.setattr(config, "command_total_timeout", 90.0)
    monkeypatch.setattr(uc, "read_status_file", lambda *_: None)
    monkeypatch.setattr(
        uc.UnityConnection,
        "connect",
        lambda *args, **kwargs: pytest.fail("unexpected reconnect in fake socket fixture"),
    )
    pool = uc.UnityConnectionPool()
    monkeypatch.setattr(uc, "_unity_connection_pool", pool)
    pool._default_instance_id = "Main@deadbeef"
    sockets = {}
    for index, identity in enumerate(("Main@deadbeef", "Other@cafebabe")):
        name, hash_value = identity.split("@")
        info = UnityInstanceInfo(
            id=identity,
            name=name,
            hash=hash_value,
            path=f"/Owned/{name}/Assets",
            port=6400 + index,
            status="running",
        )
        sock = ResponseSocket()
        sockets[identity] = sock
        pool._known_instances[identity] = info
        pool._connections[identity] = uc.UnityConnection(
            port=info.port, instance_id=identity, sock=sock, use_framing=True
        )
    pool._last_full_scan = time.monotonic()
    sockets["Other@cafebabe"].release.set()
    monkeypatch.setattr(uc, "get_unity_connection_pool", lambda: pool)
    try:
        yield pool, sockets
    finally:
        for sock in sockets.values():
            sock.release.set()


async def request(tag, selector="Main@deadbeef"):
    return await uc.async_send_command_with_retry(
        "manage_scene", {"action": "get_active", "tag": tag}, instance_id=selector
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command,params,custom,allowed",
    [
        ("ping", {}, False, True),
        ("manage_build", {"action": "status"}, False, True),
        ("ping", {}, True, False),
        ("execute_custom_tool", {"action": "status"}, False, False),
    ],
)
async def test_direct_stdio_recovery_uses_only_reviewed_control_reserve(
    environment, monkeypatch, command, params, custom, allowed
):
    from services.registry import operation_invocation
    from services.registry.operation_scheduler import (
        OperationAdmission,
        OperationScheduler,
        OperationScope,
    )

    _, sockets = environment
    sockets["Main@deadbeef"].release.set()
    scheduler = OperationScheduler(max_participants=1)
    monkeypatch.setattr(operation_invocation, "scheduler", lambda: scheduler)
    ordinary = OperationAdmission(OperationScope("local", "busy", "g1"), "exclusive", lambda: True)
    async with scheduler.session(ordinary):
        result = await uc.async_send_command_with_retry(
            command,
            {**params, "tag": "control"},
            instance_id="Main@deadbeef",
            _catalog_guard=object() if custom else None,
        )
        if allowed:
            expected = (
                {"message": "pong"} if command == "ping" else {"success": True, "tag": "control"}
            )
            assert result == expected
        else:
            assert result.success is False
            assert result.error == "operation_capacity"
        assert sockets["Main@deadbeef"].tags == (
            ["ping" if command == "ping" else "control"] if allowed else []
        )
    assert scheduler.snapshot()["retained_bytes"] == 0
    assert scheduler._controls == {}


@pytest.mark.asyncio
async def test_cancelled_stdio_recovery_retains_reserve_until_socket_worker_settles(
    environment, monkeypatch
):
    from services.registry import operation_invocation
    from services.registry.operation_scheduler import OperationScheduler

    _, sockets = environment
    main = sockets["Main@deadbeef"]
    scheduler = OperationScheduler(max_participants=0, max_control_per_instance=1)
    monkeypatch.setattr(operation_invocation, "scheduler", lambda: scheduler)
    caller = asyncio.create_task(
        uc.async_send_command_with_retry("ping", {"tag": "first"}, instance_id="Main@deadbeef")
    )
    try:
        await wait_event(main.sent)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        result = await uc.async_send_command_with_retry(
            "ping", {"tag": "blocked"}, instance_id="Main@deadbeef"
        )
        assert result.success is False
        assert result.error == "operation_capacity"
        assert main.tags == ["ping"]
        assert len(scheduler._controls) == 1
    finally:
        main.release.set()
        caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
    async with asyncio.timeout(watchdog_timeout()):
        while scheduler._controls:
            await asyncio.sleep(0)
    result = await uc.async_send_command_with_retry(
        "ping", {"tag": "following"}, instance_id="Main@deadbeef"
    )
    assert result == {"message": "pong"}
    assert scheduler.snapshot()["retained_bytes"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selector", [None, "Main", "dead", "6400", "/Owned/Main/Assets", "Main@deadbeef"]
)
async def test_alias_waiters_share_actual_connection_admission(environment, selector):
    _, sockets = environment
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=2))
    main = sockets["Main@deadbeef"]
    first = asyncio.create_task(request("first"))
    await wait_event(main.sent)
    waiter = asyncio.create_task(request("alias", selector))
    try:
        # The short task is queued after the same-Editor request. It must run
        # before the held Main response is released, even with only two workers.
        other = asyncio.create_task(request("other", "Other@cafebabe"))
        response, marker = await asyncio.wait_for(
            asyncio.gather(other, asyncio.to_thread(lambda: "free")), watchdog_timeout()
        )
        assert response == {"success": True, "tag": "other"}
        assert marker == "free"
        assert main.tags == ["first"]
    finally:
        main.release.set()
        results = await asyncio.gather(first, waiter, return_exceptions=True)
    assert results == [{"success": True, "tag": "first"}, {"success": True, "tag": "alias"}]
    assert main.tags == ["first", "alias"]
    assert main.gettimeout() == 30.0


@pytest.mark.asyncio
async def test_cancelled_admission_waiter_never_dispatches(environment, monkeypatch):
    _, sockets = environment
    main = sockets["Main@deadbeef"]
    first = asyncio.create_task(request("first"))
    await wait_event(main.sent)
    lookup_finished = threading.Event()
    original_lookup = uc.get_unity_connection

    def lookup(*args):
        conn = original_lookup(*args)
        lookup_finished.set()
        return conn

    monkeypatch.setattr(uc, "get_unity_connection", lookup)
    second = asyncio.create_task(request("cancelled"))
    await wait_event(lookup_finished)
    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second
    main.release.set()
    await first
    await asyncio.to_thread(lambda: None)
    assert main.tags == ["first"]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["preflight", "socket_lock", "header"])
async def test_cancelled_before_business_payload_cannot_escape_worker_preflight(
    environment, monkeypatch, phase
):
    pool, sockets = environment
    main = sockets["Main@deadbeef"]
    conn = pool._connections["Main@deadbeef"]
    conn.authenticated_session = StdioAuthenticatedSession("transport", "session", frozenset())
    conn.session_generation = conn.authenticated_session.session_generation
    monkeypatch.setattr(uc, "get_unity_connection", lambda _: conn)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original_send = uc._send_command_with_retry
    original_lock = conn._command_lock
    original_write = main.sendall

    def send(*args, **kwargs):
        try:
            return original_send(*args, **kwargs)
        finally:
            finished.set()

    def status(*_):
        entered.set()
        assert release.wait(watchdog_timeout())
        return None

    @contextmanager
    def command_lock(deadline):
        entered.set()
        with original_lock(deadline):
            yield

    def write(payload):
        original_write(payload)
        if len(payload) == 8:
            entered.set()
            assert release.wait(watchdog_timeout())

    monkeypatch.setattr(uc, "_send_command_with_retry", send)
    if phase == "preflight":
        monkeypatch.setattr(uc, "read_status_file", status)
    elif phase == "socket_lock":
        conn._io_lock.acquire()
        monkeypatch.setattr(conn, "_command_lock", command_lock)
    else:
        monkeypatch.setattr(main, "sendall", write)
    main.release.set()
    task = asyncio.create_task(
        uc.async_send_command_with_retry(
            "manage_gameobject",
            {"action": "delete", "tag": "cancelled"},
            instance_id=conn.instance_id,
        )
    )
    try:
        await wait_event(entered)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert main.tags == []
    finally:
        release.set()
        if phase == "socket_lock":
            conn._io_lock.release()
        await wait_event(finished)
        await asyncio.gather(task, return_exceptions=True)
    assert main.tags == [], "A cancelled caller dispatched its first mutation payload"
    assert main.header_pending is (phase != "header")
    assert main.closed is (phase == "header")
    assert (conn.sock is None) is (phase == "header")


@pytest.mark.asyncio
async def test_cancelled_payload_sent_call_retains_unknown_outcome_without_replay(
    environment, monkeypatch
):
    _, sockets = environment
    main = sockets["Main@deadbeef"]
    finished = threading.Event()
    outcomes = []
    original = uc._send_command_with_retry

    def send(*args, **kwargs):
        try:
            outcome = original(*args, **kwargs)
            outcomes.append(outcome[0])
            return outcome
        finally:
            finished.set()

    def receive(_count, flags=0):
        if flags:
            raise BlockingIOError()
        assert main.release.wait(watchdog_timeout())
        raise socket.timeout("response lost after cancellation")

    monkeypatch.setattr(uc, "_send_command_with_retry", send)
    monkeypatch.setattr(main, "recv", receive)
    task = asyncio.create_task(request("already-sent"))
    try:
        await wait_event(main.sent)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        main.release.set()
        await wait_event(finished)
        await asyncio.gather(task, return_exceptions=True)
    assert main.tags == ["already-sent"]
    assert outcomes[0].data["reason"] == "outcome_unknown"
    assert outcomes[0].hint == "inspect_state_before_retry"
    assert main.closed


@pytest.mark.asyncio
async def test_cancelled_after_progress_prelude_discards_pending_peer_association(
    environment, monkeypatch
):
    pool, sockets = environment
    main = sockets["Main@deadbeef"]
    conn = pool._connections["Main@deadbeef"]
    conn.authenticated_session = StdioAuthenticatedSession(
        "transport", "session", frozenset({"command_progress_v1"})
    )
    conn.session_generation = conn.authenticated_session.session_generation
    monkeypatch.setattr(uc, "get_unity_connection", lambda _: conn)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original_send, original_write = uc._send_command_with_retry, main.sendall
    writes = []

    def send(*args, **kwargs):
        try:
            return original_send(*args, **kwargs)
        finally:
            finished.set()

    def write(payload):
        writes.append(payload)
        if len(payload) != 8 and json.loads(payload).get("type") == "command_progress_begin":
            main.header_pending = True
            entered.set()
            assert release.wait(watchdog_timeout())
            return
        original_write(payload)

    monkeypatch.setattr(uc, "_send_command_with_retry", send)
    monkeypatch.setattr(main, "sendall", write)
    sink = SimpleNamespace(bind=lambda _: True, close=lambda: None)
    task = asyncio.create_task(
        uc.async_send_command_with_retry(
            "batch_execute",
            {"commands": [{"tool": "manage_gameobject", "params": {"action": "delete"}}]},
            instance_id=conn.instance_id,
            _progress=sink,
        )
    )
    try:
        await wait_event(entered)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        await wait_event(finished)
        await asyncio.gather(task, return_exceptions=True)
    assert len(writes) == 2, "Only the association prelude may have reached the peer"
    assert main.tags == []
    assert main.closed and conn.sock is None


@pytest.mark.asyncio
async def test_cancelled_inflight_call_holds_admission_until_worker_finishes(
    environment, monkeypatch
):
    pool, sockets = environment
    main = sockets["Main@deadbeef"]
    conn = pool._connections["Main@deadbeef"]
    conn._needs_tool_resync = True
    calls = []
    original = uc._send_command_with_retry

    def traced(*args, **kwargs):
        calls.append(args[1]["tag"])
        return original(*args, **kwargs)

    monkeypatch.setattr(uc, "_send_command_with_retry", traced)
    first = asyncio.create_task(request("cancelled-inflight"))
    await wait_event(main.sent)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    lookup_finished = threading.Event()
    original_lookup = uc.get_unity_connection

    def lookup(*args):
        result = original_lookup(*args)
        lookup_finished.set()
        return result

    monkeypatch.setattr(uc, "get_unity_connection", lookup)
    second = asyncio.create_task(request("following"))
    try:
        await wait_event(lookup_finished)
        assert calls == ["cancelled-inflight"]
        assert conn._needs_tool_resync is True
        assert main.tags == ["cancelled-inflight"]
        # Avoid scheduling real catalog resync in this focused transport test.
        conn._needs_tool_resync = False
    finally:
        main.release.set()
        await second
    assert main.tags == ["cancelled-inflight", "following"]


@pytest.mark.asyncio
async def test_admission_wait_consumes_original_deadline_without_dispatch(environment, monkeypatch):
    _, sockets = environment
    main = sockets["Main@deadbeef"]
    first = asyncio.create_task(request("held"))
    await wait_event(main.sent)
    monkeypatch.setattr(config, "command_total_timeout", 0.05)
    try:
        result = await request("expired")
        assert result.success is False
        assert "deadline" in result.error.lower()
        assert main.tags == ["held"]
    finally:
        main.release.set()
        await first


@pytest.mark.asyncio
async def test_selection_consumes_request_deadline(environment, monkeypatch):
    pool, sockets = environment
    main = sockets["Main@deadbeef"]
    main.release.set()
    conn = pool._connections["Main@deadbeef"]
    lookup_finished = threading.Event()

    def slow_lookup(*args):
        time.sleep(0.1)
        lookup_finished.set()
        return conn

    monkeypatch.setattr(uc, "get_unity_connection", slow_lookup)
    monkeypatch.setattr(config, "command_total_timeout", 0.03)
    result = await request("expired-selection")
    assert result.success is False
    assert "deadline" in result.error.lower()
    await wait_event(lookup_finished)
    assert main.tags == []


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_executor_queued_command_aborts_before_dispatch(environment, monkeypatch, cancel):
    pool, sockets = environment
    main = sockets["Main@deadbeef"]
    main.release.set()
    conn = pool._connections["Main@deadbeef"]
    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=1)
    loop.set_default_executor(executor)
    diagnostics = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: diagnostics.append(context))
    release_blocker = threading.Event()
    blocker_started = threading.Event()

    def blocker():
        blocker_started.set()
        assert release_blocker.wait(watchdog_timeout())

    def lookup(*args):
        # Selection worker queues unrelated work before returning. Consequently
        # the admitted command worker is pending behind it in the executor.
        executor.submit(blocker)
        return conn

    monkeypatch.setattr(uc, "get_unity_connection", lookup)
    monkeypatch.setattr(config, "command_total_timeout", 0.1)
    task = asyncio.create_task(request("queued"))
    try:
        await wait_event(blocker_started)
        await asyncio.sleep(0)
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            result = await asyncio.wait_for(task, watchdog_timeout())
            assert result.success is False
            assert "deadline" in result.error.lower()
        assert main.tags == []
    finally:
        release_blocker.set()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.to_thread(lambda: None)
        await asyncio.sleep(0)
        loop.set_exception_handler(previous_handler)
    assert main.tags == []
    assert diagnostics == [], "Aborted private worker completion leaked an asyncio error"


@pytest.mark.asyncio
async def test_dispatched_response_timeout_preserves_unknown_outcome_and_does_not_replay(
    environment, monkeypatch
):
    _, sockets = environment
    main = sockets["Main@deadbeef"]

    def expire_response(_count, flags=0):
        if flags:
            raise BlockingIOError()
        assert main.sent.is_set(), "The response timeout must follow actual payload dispatch"
        raise socket.timeout("controlled dispatched response timeout")

    monkeypatch.setattr(main, "recv", expire_response)
    result = await request("unknown")
    assert result.success is False
    assert result.data["reason"] == "outcome_unknown"
    assert result.hint == "inspect_state_before_retry"
    assert main.tags == ["unknown"]
    assert main.closed is True


def test_connection_reused_across_loops_does_not_retain_closed_loops(environment):
    _, sockets = environment
    main = sockets["Main@deadbeef"]
    references = []

    async def call(index):
        references.append(weakref.ref(asyncio.get_running_loop()))
        main.sent.clear()
        main.release.clear()
        first = asyncio.create_task(request(f"{index}-first"))
        await wait_event(main.sent)
        second = asyncio.create_task(request(f"{index}-second"))
        # Actual contention binds the asyncio gate to this loop. Closed-loop
        # collection must work even after its lock has queued a waiter.
        timer = threading.Timer(0.02, main.release.set)
        timer.start()
        try:
            responses = await asyncio.gather(first, second)
            assert all(response["success"] for response in responses)
        finally:
            main.release.set()
            timer.join(watchdog_timeout())

    for index in range(3):
        asyncio.run(call(index))
    gc.collect()
    assert all(reference() is None for reference in references)
    assert main.tags == [
        f"{index}-{position}" for index in range(3) for position in ("first", "second")
    ]


@pytest.mark.asyncio
async def test_ambiguous_default_does_not_admit_or_send(environment):
    pool, sockets = environment
    pool._default_instance_id = None
    response = await request("ambiguous", None)
    assert response.success is False
    assert "Multiple Unity instances" in response.error
    assert all(sock.tags == [] for sock in sockets.values())


@pytest.mark.asyncio
async def test_cancelled_selection_never_dispatches_or_claims_resync(environment, monkeypatch):
    pool, sockets = environment
    conn = pool._connections["Main@deadbeef"]
    conn._needs_tool_resync = True
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def lookup(*args):
        started.set()
        assert release.wait(watchdog_timeout())
        finished.set()
        return conn

    monkeypatch.setattr(uc, "get_unity_connection", lookup)
    task = asyncio.create_task(request("cancelled-selection"))
    try:
        await wait_event(started)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        await wait_event(finished)
    await asyncio.sleep(0)
    assert sockets["Main@deadbeef"].tags == []
    assert conn._needs_tool_resync is True


@pytest.mark.asyncio
async def test_async_admission_preserves_public_sync_socket_serialization(environment, monkeypatch):
    _, sockets = environment
    main = sockets["Main@deadbeef"]
    first = asyncio.create_task(request("async"))
    await wait_event(main.sent)
    lookup_finished = threading.Event()
    original_lookup = uc.get_unity_connection

    def lookup(*args):
        conn = original_lookup(*args)
        lookup_finished.set()
        return conn

    monkeypatch.setattr(uc, "get_unity_connection", lookup)
    with ThreadPoolExecutor(max_workers=1) as executor:
        synchronous = executor.submit(
            uc.send_command_with_retry, "manage_scene", {"tag": "sync"}, instance_id="Main@deadbeef"
        )
        try:
            await wait_event(lookup_finished)
            assert main.tags == ["async"]
            main.release.set()
            assert (await first)["success"]
            assert (await asyncio.wrap_future(synchronous))["success"]
        finally:
            main.release.set()
    assert main.tags == ["async", "sync"]
    assert main.gettimeout() == 30.0
