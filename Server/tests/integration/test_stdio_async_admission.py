"""Async commands wait without occupying workers needed by other Editors."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import gc
import json
import socket
import struct
import threading
import time
import weakref

import pytest

from core.config import config
from models.models import UnityInstanceInfo
import transport.legacy.unity_connection as uc


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
        tag = json.loads(payload)["params"]["tag"]
        self.tags.append(tag)
        response = json.dumps(
            {"status": "success", "result": {"success": True, "tag": tag}}
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
    pool._last_full_scan = time.time()
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
            asyncio.gather(other, asyncio.to_thread(lambda: "free")), 0.5
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
        assert release_blocker.wait(2)

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
            result = await asyncio.wait_for(task, 0.5)
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
async def test_inflight_deadline_preserves_unknown_outcome_and_does_not_replay(
    environment, monkeypatch
):
    _, sockets = environment
    monkeypatch.setattr(config, "command_total_timeout", 0.03)
    result = await request("unknown")
    assert result.success is False
    assert result.data["reason"] == "outcome_unknown"
    assert result.hint == "inspect_state_before_retry"
    assert sockets["Main@deadbeef"].tags == ["unknown"]
    assert sockets["Main@deadbeef"].closed is True


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
            timer.join(2)

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
        assert release.wait(2)
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
