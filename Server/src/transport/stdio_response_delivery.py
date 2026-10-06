"""Response reservations through the SDK's actual stdio write and flush."""
from __future__ import annotations

import inspect
import asyncio
import sys
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import BinaryIO

import anyio
from mcp.server import stdio as sdk_stdio
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCError, JSONRPCResponse, jsonrpc_message_adapter

from models.response_limits import ResponseOwner


@dataclass
class _PendingDelivery:
    owner: ResponseOwner
    handed_off: bool = False
    ambiguous: bool = False


stdio_delivery: ContextVar[StdioResponseDelivery | None] = ContextVar("unity_stdio_delivery", default=None)


class StdioResponseDelivery:
    """One stdio generation; typed request IDs never cross-release copies."""

    def __init__(self, max_pending: int = 256):
        self.pending: dict[tuple[type, str | int], _PendingDelivery] = {}
        self.max_pending = max_pending
        self.closed = False

    def register(self, request_id: str | int, owner: ResponseOwner) -> _PendingDelivery:
        if type(request_id) not in (str, int) or len(str(request_id)) > 4096:
            raise ValueError("MCP response identity exceeds supported limits")
        key = (type(request_id), request_id)
        if key in self.pending:
            # The SDK emits a duplicate's error with the original ID. Neither
            # response identifies its producer, so retain through stream exit.
            self.pending[key].ambiguous = True
            raise ValueError("MCP stdio response identity is already active")
        if self.closed or len(self.pending) >= self.max_pending:
            raise ValueError("MCP stdio response delivery capacity unavailable")
        entry = _PendingDelivery(owner)
        self.pending[key] = entry

        def forget() -> None:
            if self.pending.get(key) is entry:
                self.pending.pop(key, None)

        owner.on_release.append(forget)
        return entry

    def handoff(self, session_message) -> None:
        message = session_message.message
        if isinstance(message, (JSONRPCResponse, JSONRPCError)):
            entry = self.pending.get((type(message.id), message.id))
            if entry is not None:
                # A cancelled stream send can already have delivered a message.
                # Retain conservatively from send start through flush/teardown.
                entry.handed_off = True

    def producer_done(self, entry: _PendingDelivery, failed: bool) -> None:
        if failed and not entry.handed_off and not entry.ambiguous:
            entry.owner.release()

    def close(self) -> None:
        self.closed = True
        for entry in tuple(self.pending.values()):
            entry.owner.release()


class DeliverySendStream:
    """Public SDK memory stream interface with an ownership handoff marker."""

    def __init__(self, stream, delivery: StdioResponseDelivery):
        self.stream, self.delivery = stream, delivery

    async def send(self, message) -> None:
        self.delivery.handoff(message)
        await self.stream.send(message)

    def send_nowait(self, message) -> None:
        self.delivery.handoff(message)
        self.stream.send_nowait(message)

    def clone(self):
        return DeliverySendStream(self.stream.clone(), self.delivery)

    async def aclose(self) -> None:
        await self.stream.aclose()

    async def __aenter__(self):
        await self.stream.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self.stream.__aexit__(*args)


class DeliveryWriter:
    """The SDK calls write then flush serially; acknowledge only after both."""

    def __init__(self, stdout, delivery: StdioResponseDelivery):
        self.stdout, self.delivery = stdout, delivery
        self.current: _PendingDelivery | None = None

    async def write(self, text: str):
        from transport.response_limit_middleware import _frame_id
        request_id = _frame_id(text[:65_536].encode("utf-8"))
        self.current = self.delivery.pending.get((type(request_id), request_id))
        try:
            return await self.stdout.write(text)
        except BaseException:
            self._release_current()
            raise

    async def flush(self) -> None:
        try:
            await self.stdout.flush()
        finally:
            self._release_current()

    def _release_current(self) -> None:
        if self.current is not None:
            if not self.current.ambiguous:
                self.current.owner.release()
            self.current = None


def _claim_sdk_stdout():
    """Verified MCP 2.3 compatibility seam; preserve its protected FD claim."""
    claim = getattr(sdk_stdio, "_claim_fd", None)
    diversion = getattr(sdk_stdio, "_open_stdout_diversion", None)
    wrapper = getattr(sdk_stdio, "_UnownedTextWrapper", None)
    if (not callable(claim) or not callable(diversion) or not callable(wrapper)
            or tuple(inspect.signature(claim).parameters) != ("fd", "stream", "mode", "open_diversion")):
        raise RuntimeError("Installed MCP stdio ownership API is unsupported")
    return claim(1, sys.stdout, "wb", diversion)


def _claim_sdk_stdin():
    """Preserve the pinned SDK's protected input descriptor and text parser."""
    claim = getattr(sdk_stdio, "_claim_fd", None)
    diversion = getattr(sdk_stdio, "_open_stdin_diversion", None)
    wrapper = getattr(sdk_stdio, "_UnownedTextWrapper", None)
    if (not callable(claim) or not callable(diversion) or not callable(wrapper)
            or tuple(inspect.signature(claim).parameters) != ("fd", "stream", "mode", "open_diversion")):
        raise RuntimeError("Installed MCP stdio ownership API is unsupported")
    buffer, restore = claim(0, sys.stdin, "rb", diversion)
    try:
        return anyio.wrap_file(wrapper(buffer, encoding="utf-8", errors="replace")), restore
    except BaseException:
        if restore is not None:
            restore()
        raise


def _write_binary_frame(stdout: BinaryIO, payload: bytes) -> None:
    """Write exact UTF-8 bytes and LF, including partial writes, then flush."""
    for part in (payload, b"\n"):
        with memoryview(part) as view:
            offset = 0
            while offset < len(view):
                written = stdout.write(view[offset:])
                if written is None or written <= 0:
                    raise BrokenPipeError("MCP stdio writer made no progress")
                offset += written
    stdout.flush()


@dataclass
class _BinaryWriteOperation:
    stdout: BinaryIO
    payload: bytes

    def run(self) -> None:
        # AnyIO's idle worker can retain its last job arguments. Pass this
        # holder rather than the large bytes and clear it after actual flush.
        try:
            _write_binary_frame(self.stdout, self.payload)
        finally:
            self.payload = b""


async def _settled_binary_write(stdout: BinaryIO, payload: bytes) -> None:
    """Cancellation cannot release a response while its worker holds bytes."""
    with anyio.CancelScope(shield=True):
        operation = _BinaryWriteOperation(stdout, payload)
        worker = asyncio.create_task(anyio.to_thread.run_sync(operation.run))
        cancelled = False
        while True:
            try:
                await asyncio.shield(worker)
                break
            except asyncio.CancelledError:
                cancelled = True
                if worker.cancelled():
                    raise
        if cancelled:
            raise asyncio.CancelledError


@asynccontextmanager
async def _binary_stdio_server(stdin, stdout: BinaryIO, delivery: StdioResponseDelivery):
    """Pinned SDK stream topology with a bytes-only output consumer."""
    streams = getattr(sdk_stdio, "create_context_streams", None)
    if not callable(streams) or tuple(inspect.signature(streams).parameters) != ("max_buffer_size",):
        raise RuntimeError("Installed MCP stdio context stream API is unsupported")
    read_writer, read = streams(0)
    write, write_reader = streams(0)

    async def input_reader():
        try:
            async with read_writer:
                async for line in stdin:
                    try:
                        message = jsonrpc_message_adapter.validate_json(line, by_name=False)
                    except Exception as exc:
                        await read_writer.send(exc)
                        continue
                    await read_writer.send(SessionMessage(message))
        except anyio.ClosedResourceError:
            await anyio.lowlevel.checkpoint()

    async def output_writer():
        try:
            async with write_reader:
                async for envelope in write_reader:
                    message = envelope.message
                    entry = None
                    if isinstance(message, (JSONRPCResponse, JSONRPCError)):
                        entry = delivery.pending.get((type(message.id), message.id))
                    try:
                        payload = jsonrpc_message_adapter.dump_json(message, by_alias=True, exclude_unset=True)
                        await _settled_binary_write(stdout, payload)
                    finally:
                        payload = message = envelope = None
                        if entry is not None and not entry.ambiguous:
                            entry.owner.release()
        except anyio.ClosedResourceError:
            await anyio.lowlevel.checkpoint()

    async with read_writer, read, write, write_reader, anyio.create_task_group() as group:
        group.start_soon(input_reader)
        group.start_soon(output_writer)
        yield read, write


@asynccontextmanager
async def retained_stdio_server(stdin=None, stdout=None, *, binary_stdout: BinaryIO | None = None):
    """Use public SDK streams, retaining its default wire diversion/restore."""
    delivery = StdioResponseDelivery()
    token = stdio_delivery.set(delivery)
    restore = None
    restore_stdin = None
    try:
        if stdout is not None:
            if binary_stdout is not None:
                raise ValueError("Supply either text or binary MCP stdout")
            async with sdk_stdio.stdio_server(stdin=stdin, stdout=DeliveryWriter(stdout, delivery)) as (read, write):
                yield read, DeliverySendStream(write, delivery)
        else:
            if binary_stdout is None:
                binary_stdout, restore = _claim_sdk_stdout()
            if stdin is None:
                stdin, restore_stdin = _claim_sdk_stdin()
            async with _binary_stdio_server(stdin, binary_stdout, delivery) as (read, write):
                yield read, DeliverySendStream(write, delivery)
    finally:
        delivery.close()
        stdio_delivery.reset(token)
        try:
            if restore is not None:
                restore()
        finally:
            if restore_stdin is not None:
                restore_stdin()
