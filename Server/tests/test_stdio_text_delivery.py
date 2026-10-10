"""Explicit text stdout owns responses until real flush completion."""

import asyncio
import gc
import threading
import weakref

import anyio
import pytest
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCResponse

from models.response_limits import ResponseOwner
from transport.stdio_response_delivery import retained_stdio_server, stdio_delivery
from .test_stdio_response_delivery import IdleInput


class DiscardText:
    def __init__(self):
        self.flushed = asyncio.Event()
        self.wire = None

    async def write(self, text):
        # Only preserve a small exact-wire assertion; never keep large payloads.
        if len(text) < 1024:
            self.wire = text

    async def flush(self):
        self.flushed.set()


async def send_response(write, payload):
    response = JSONRPCResponse(jsonrpc="2.0", id=7, result={"data": payload})
    reference = weakref.ref(response)
    await write.send(SessionMessage(response))
    return reference


async def wait_until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_text_output_releases_last_response_reference_before_idle_receive():
    source, output = IdleInput(), DiscardText()
    owner, ledger = ResponseOwner(), {"copy": 65_536}
    owner.entries.append((ledger, "copy"))
    async with retained_stdio_server(stdin=source, stdout=output) as (read, write):
        async with read, write:
            stdio_delivery.get().register(7, owner)
            reference = await send_response(write, "x" * 65_536)
            await wait_until(lambda: owner.released)
            for _ in range(5):
                await asyncio.sleep(0)
            gc.collect()
            try:
                assert not ledger
                assert reference() is None, (
                    "flushed text response remains retained by the idle output consumer"
                )
            finally:
                source.stop.set()


@pytest.mark.asyncio
async def test_text_output_keeps_installed_sdk_serialization_and_newline():
    source, output = IdleInput(), DiscardText()
    response = JSONRPCResponse(jsonrpc="2.0", id=7, result={"data": "owned\n\ud55c\uae00"})
    expected = response.model_dump_json(by_alias=True, exclude_unset=True) + "\n"
    async with retained_stdio_server(stdin=source, stdout=output) as (read, write):
        async with read, write:
            await write.send(SessionMessage(response))
            await asyncio.wait_for(output.flushed.wait(), 3)
            assert output.wire == expected
            source.stop.set()


@pytest.mark.asyncio
async def test_async_file_drops_serialized_and_wire_text_while_workers_idle():
    references = []

    class TrackedText(str):
        def __new__(cls, value):
            text = super().__new__(cls, value)
            references.append(weakref.ref(text))
            return text

        def __add__(self, suffix):
            return TrackedText(super().__add__(suffix))

    class TrackedResponse(JSONRPCResponse):
        def model_dump_json(self, *args, **kwargs):
            return TrackedText(super().model_dump_json(*args, **kwargs))

    class DiscardFile:
        def write(self, text):
            return len(text)

        def flush(self):
            pass

    # Populate the real pool with concurrent unrelated jobs before the writer.
    barrier = threading.Barrier(2)
    await asyncio.gather(*(anyio.to_thread.run_sync(lambda: barrier.wait(3)) for _ in range(2)))
    owner, source = ResponseOwner(), IdleInput()
    async with retained_stdio_server(stdin=source, stdout=anyio.wrap_file(DiscardFile())) as (
        read,
        write,
    ):
        async with read, write:
            stdio_delivery.get().register(7, owner)
            await write.send(
                SessionMessage(TrackedResponse(jsonrpc="2.0", id=7, result={"data": "x" * 65_536}))
            )
            await wait_until(lambda: owner.released)
            try:
                await wait_until(lambda: all(reference() is None for reference in references))
                assert len(references) == 2  # serialized text and the LF-appended wire text
            finally:
                source.stop.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("ambiguous", [False, True])
async def test_text_output_keeps_owner_through_held_flush_and_duplicate_identity(ambiguous):
    source, owner, ledger = IdleInput(), ResponseOwner(), {"copy": 100}
    owner.entries.append((ledger, "copy"))
    entered, resume = asyncio.Event(), asyncio.Event()

    class HeldFlush(DiscardText):
        async def flush(self):
            entered.set()
            await resume.wait()
            self.flushed.set()

    output = HeldFlush()
    async with retained_stdio_server(stdin=source, stdout=output) as (read, write):
        async with read, write:
            registry = stdio_delivery.get()
            registry.register(7, owner)
            if ambiguous:
                with pytest.raises(ValueError, match="already active"):
                    registry.register(7, ResponseOwner())
            await send_response(write, "owned")
            await asyncio.wait_for(entered.wait(), 3)
            assert ledger and not owner.released
            resume.set()
            await asyncio.wait_for(output.flushed.wait(), 3)
            for _ in range(5):
                await asyncio.sleep(0)
            assert owner.released is not ambiguous
            assert bool(ledger) is ambiguous
            source.stop.set()
    assert owner.released and not ledger


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["write", "flush"])
async def test_text_output_error_clears_response_and_owner_after_teardown(phase):
    source, owner, ledger = IdleInput(), ResponseOwner(), {"copy": 100}
    owner.entries.append((ledger, "copy"))
    references = []

    class BrokenOutput(DiscardText):
        async def write(self, text):
            if phase == "write":
                raise BrokenPipeError("owned write failure")

        async def flush(self):
            raise BrokenPipeError("owned flush failure")

    with pytest.raises(BaseExceptionGroup) as failure:
        async with retained_stdio_server(stdin=source, stdout=BrokenOutput()) as (read, write):
            async with read, write:
                stdio_delivery.get().register(7, owner)
                references.append(await send_response(write, "x" * 65_536))
                await asyncio.Future()
    assert "owned " in str(failure.value.exceptions[0])
    assert owner.released and not ledger
    # Exception tracebacks can own transient serializer locals until disposed.
    del failure
    gc.collect()
    assert all(reference() is None for reference in references)


def output_consumer_task():
    for task in asyncio.all_tasks():
        coroutine = task.get_coro()
        while coroutine is not None:
            name = getattr(getattr(coroutine, "cr_code", None), "co_name", None)
            if name in ("output_writer", "stdout_writer"):
                return task
            coroutine = getattr(coroutine, "cr_await", None)
    raise AssertionError("owned output consumer not running")


@pytest.mark.asyncio
async def test_opaque_async_text_sink_settles_its_cancel_cleanup_before_owner_release():
    owner, ledger = ResponseOwner(), {"copy": 100}
    owner.entries.append((ledger, "copy"))
    entered, cleaning, complete = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class CancellableSink(DiscardText):
        async def write(self, text):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                with anyio.CancelScope(shield=True):
                    cleaning.set()
                    await complete.wait()

    async def serve():
        async with retained_stdio_server(stdin=IdleInput(), stdout=CancellableSink()) as (
            read,
            write,
        ):
            async with read, write:
                stdio_delivery.get().register(7, owner)
                await send_response(write, "owned")
                await asyncio.Future()

    server = asyncio.create_task(serve())
    try:
        await asyncio.wait_for(entered.wait(), 3)
        server.cancel()
        await asyncio.wait_for(cleaning.wait(), 3)
        assert ledger and not owner.released and not server.done()
    finally:
        complete.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(server, 3)
    assert owner.released and not ledger


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phase,wrapper_kind",
    [
        ("write", "base"),
        ("flush", "base"),
        ("write", "inherited"),
        ("flush", "inherited"),
        ("write", "override_flush"),
        ("flush", "override_write"),
    ],
)
@pytest.mark.parametrize("cancel_target", ["server", "writer", "writer_repeated"])
async def test_text_output_cancellation_waits_for_real_thread_io_before_releasing(
    phase, cancel_target, wrapper_kind
):
    owner, ledger = ResponseOwner(), {"copy": 65_536}
    owner.entries.append((ledger, "copy"))
    entered, resume, settled = threading.Event(), threading.Event(), threading.Event()
    references = []

    class ThreadTextSink:
        def write(self, text):
            if phase == "write":
                self.hold()
            return len(text)

        def flush(self):
            if phase == "flush":
                self.hold()

        def hold(self):
            entered.set()
            try:
                assert resume.wait(5), "test failed to unblock the owned flush worker"
            finally:
                settled.set()

    async def serve():
        class InheritedAsyncFile(anyio.AsyncFile):
            pass

        class FlushOverride(anyio.AsyncFile):
            async def flush(self):
                return None

        class WriteOverride(anyio.AsyncFile):
            async def write(self, text):
                return len(text)

        adapter = {
            "base": anyio.AsyncFile,
            "inherited": InheritedAsyncFile,
            "override_flush": FlushOverride,
            "override_write": WriteOverride,
        }[wrapper_kind]
        output = adapter(ThreadTextSink())
        async with retained_stdio_server(stdin=IdleInput(), stdout=output) as (read, write):
            async with read, write:
                stdio_delivery.get().register(7, owner)
                references.append(await send_response(write, "x" * 65_536))
                await asyncio.Future()

    server = asyncio.create_task(serve())
    try:
        await wait_until(entered.is_set)
        target = server if cancel_target == "server" else output_consumer_task()
        target.cancel()
        if cancel_target == "writer_repeated":
            await asyncio.sleep(0)
            target.cancel()
        for _ in range(10):
            await asyncio.sleep(0)
        assert not settled.is_set()
        assert ledger and not owner.released
        assert not server.done()
    finally:
        resume.set()
        server.cancel()
        try:
            await asyncio.wait_for(server, 3)
        except asyncio.CancelledError:
            pass
    assert settled.is_set() and owner.released and not ledger
    del server
    gc.collect()
    assert all(reference() is None for reference in references)
