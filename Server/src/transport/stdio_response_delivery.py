"""Response reservations through the SDK's actual stdio write and flush."""
from __future__ import annotations

import inspect
import sys
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass

import anyio
from mcp.server import stdio as sdk_stdio
from mcp.types import JSONRPCError, JSONRPCResponse

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
    buffer, restore = claim(1, sys.stdout, "wb", diversion)
    try:
        return anyio.wrap_file(wrapper(buffer, encoding="utf-8")), restore
    except BaseException:
        if restore is not None:
            restore()
        raise


@asynccontextmanager
async def retained_stdio_server(stdin=None, stdout=None):
    """Use public SDK streams, retaining its default wire diversion/restore."""
    delivery = StdioResponseDelivery()
    token = stdio_delivery.set(delivery)
    restore = None
    try:
        if stdout is None:
            stdout, restore = _claim_sdk_stdout()
        async with sdk_stdio.stdio_server(stdin=stdin, stdout=DeliveryWriter(stdout, delivery)) as (read, write):
            yield read, DeliverySendStream(write, delivery)
    finally:
        delivery.close()
        stdio_delivery.reset(token)
        if restore is not None:
            restore()
