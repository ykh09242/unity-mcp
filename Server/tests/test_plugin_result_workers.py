"""Transport response workers retain capacity while the owner loop stays responsive."""

import asyncio
import json
from threading import Event, get_ident
from types import SimpleNamespace

import pytest

from tests.test_plugin_json_decoder import COMMAND_ID, owned_hub  # noqa: F401
from tests.test_large_result_assembler import frames
from tests.test_operation_scheduler import settled
from tests.timing import await_signal, watchdog_timeout
from transport import plugin_hub as hub_module
from transport.models import CommandResultMessage, PongMessage
from transport.large_result_assembler import LargeResultProtocolError
from transport.plugin_registry import PluginRegistry


@pytest.mark.asyncio
@pytest.mark.parametrize("large", [False, True])
@pytest.mark.parametrize(
    "ending",
    [
        "deliver",
        "cancel",
        "disconnect",
        "reconnect",
        "reconfigure",
        "replacement",
        "capacity_replacement",
        "failure",
    ],
)
async def test_result_worker_holds_budget_until_exit_and_fences_delivery(
    owned_hub, monkeypatch, large, ending
):
    hub, socket = owned_hub
    cls = type(hub)
    loop = asyncio.get_running_loop()
    owner_thread = get_ident()
    entered, release = asyncio.Event(), Event()
    future = loop.create_future()
    entry = {"future": future, "session_id": "owned", "user_id": None}
    cls._pending[COMMAND_ID] = entry
    neighbor = SimpleNamespace(state=SimpleNamespace(plugin_generation="neighbor"))
    cls._connections["neighbor"] = neighbor
    cls._last_pong["neighbor"] = 0
    expected = (
        {"text": "x" * (256 * 1024)} if large else {str(index): "x" * 60000 for index in range(100)}
    )
    native = hub_module.bounded_json_text if large else hub_module.response_size

    def held(*args, **kwargs):
        assert get_ident() != owner_thread, "Expensive result work ran on the owner loop"
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(watchdog_timeout()), "Test did not release the worker"
        if ending == "failure":
            raise ValueError("synthetic result failure")
        if large and ending == "capacity_replacement":
            raise LargeResultProtocolError("result_capacity", COMMAND_ID)
        return native(*args, **kwargs)

    monkeypatch.setattr(hub_module, "bounded_json_text" if large else "response_size", held)

    async def receive():
        if not large:
            await hub._handle_command_result(
                socket, CommandResultMessage(id=COMMAND_ID, result=expected)
            )
            return
        wire = json.dumps({"type": "command_result", "id": COMMAND_ID, "result": expected}).encode()
        chunks = frames(wire, COMMAND_ID)
        await hub._handle_large_result(
            socket,
            {
                "type": "result_start",
                "id": COMMAND_ID,
                "total_bytes": len(wire),
                "chunk_count": len(chunks),
            },
        )
        for frame in chunks:
            await hub._handle_large_result(socket, frame)

    task = asyncio.create_task(receive())
    try:
        await await_signal(entered, "result worker started", producers=(task,))
        charge = cls._retained_results.total_bytes + cls._raw_results.total_bytes
        assert charge > 0
        await hub._handle_pong(neighbor, PongMessage(session_id="neighbor"))
        assert cls._last_pong["neighbor"] > 0
        async with cls._lock:
            if ending == "cancel":
                cls._pending.pop(COMMAND_ID)
                future.cancel()
                cls._large_results.discard("owned", COMMAND_ID)
                task.cancel()
            elif ending == "reconnect":
                socket.state.plugin_generation = "replacement"
                cls._large_results.discard_owner("owned")
            elif ending == "disconnect":
                cls._connections.pop("owned")
                cls._large_results.discard_owner("owned")
            elif ending == "reconfigure":
                cls.configure(PluginRegistry())
            elif ending in {"replacement", "capacity_replacement"}:
                cls._pending[COMMAND_ID] = {**entry, "future": loop.create_future()}
        await asyncio.sleep(0)
        if ending == "cancel":
            task.cancel()
            await asyncio.sleep(0)
        assert cls._retained_results.total_bytes + cls._raw_results.total_bytes == charge
        assert not task.done()
        release.set()
        result = await asyncio.gather(task, return_exceptions=True)
        if ending == "deliver":
            assert result == [None]
            assert future.result() == expected
            cls._retained_results.pop(COMMAND_ID)
        else:
            assert not future.done() or future.cancelled()
            if ending == "failure":
                if large:
                    socket.close.assert_awaited_once()
                else:
                    assert isinstance(result[0], ValueError)
            if ending in {"replacement", "capacity_replacement"}:
                assert not cls._pending[COMMAND_ID]["future"].done()
        assert cls._retained_results.total_bytes + cls._raw_results.total_bytes == 0
        assert cls._large_results.retained_bytes == 0
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        future.cancel()


@pytest.mark.asyncio
async def test_queued_response_cancellation_releases_only_its_reserved_bytes(
    owned_hub, monkeypatch
):
    hub, socket = owned_hub
    cls = type(hub)
    loop = asyncio.get_running_loop()
    entered, release = asyncio.Event(), Event()
    started = []
    native = hub_module.response_size

    def held(value, **kwargs):
        def observe():
            started.append(value["id"])
            if len(started) == 2:
                entered.set()

        loop.call_soon_threadsafe(observe)
        assert release.wait(watchdog_timeout())
        return native(value, **kwargs)

    monkeypatch.setattr(hub_module, "response_size", held)
    tasks = []
    futures = []
    try:
        for index in range(3):
            session = f"session-{index}"
            cls._connections[session] = socket
            future = loop.create_future()
            futures.append(future)
            cls._pending[str(index)] = {"future": future, "session_id": session, "user_id": None}
            tasks.append(
                asyncio.create_task(
                    hub._handle_command_result(
                        socket,
                        CommandResultMessage(
                            id=str(index), result={"id": index, "text": "x" * (256 * 1024)}
                        ),
                    )
                )
            )
        await await_signal(entered, "two response workers", producers=tasks)
        await settled()
        assert len(started) == 2
        assert cls._retained_results.total_bytes == 3 * cls.MAX_RESULT_RETAINED_BYTES
        tasks[2].cancel()
        await asyncio.gather(tasks[2], return_exceptions=True)
        assert cls._retained_results.total_bytes == 2 * cls.MAX_RESULT_RETAINED_BYTES
        assert len(started) == 2
        release.set()
        await asyncio.gather(*tasks[:2])
        for index in range(2):
            assert futures[index].result()["id"] == index
            cls._retained_results.pop(str(index))
        assert cls._retained_results.total_bytes == 0
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        for future in futures:
            future.cancel()
