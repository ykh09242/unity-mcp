"""Bounded ownership of outbound HTTP command parameters before admission."""

import asyncio
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from threading import BoundedSemaphore, Event
from typing import Any

from services.execution_receipt_context import submit_owned_executor


class CommandPreparationBusy(Exception):
    """All command preparation slots are owned by submitted work."""


class _UseWorker(Exception):
    """The partial snapshot exceeded the inline work budget."""


@dataclass(frozen=True, slots=True)
class PreparedCommand:
    params: dict[str, Any]
    retained_bytes: int


# No waiting queue of uncharged request graphs. A cancelled waiter cannot
# release a slot still occupied by its executor job, including across Hub reset.
_workers = BoundedSemaphore(2)


def _prepare(
    command: str,
    params: dict[str, Any],
    measure: Callable[[str, dict[str, Any]], int | None],
    max_bytes: int,
    max_nodes: int,
    max_depth: int,
    *,
    inline: bool,
) -> PreparedCommand | None:
    remaining = max_nodes
    bytes_left = max_bytes - len(command)
    inline_work = 64 * 1024 - len(command)

    def snapshot(value: Any, depth: int) -> Any:
        nonlocal remaining, bytes_left, inline_work
        remaining -= 1
        if remaining < 0 or depth > max_depth or bytes_left < 0:
            raise ValueError
        kind = type(value)
        if inline and (max_nodes - remaining > 256 or inline_work < 0):
            raise _UseWorker
        if issubclass(kind, dict):
            if 2 * dict.__len__(value) > remaining:
                raise ValueError
            result = {}
            for key, child in dict.items(value):
                if not issubclass(type(key), str):
                    raise ValueError
                owned_key = snapshot(key, depth + 1)
                result[owned_key] = snapshot(child, depth + 1)
            return result
        if issubclass(kind, (list, tuple)):
            sequence = list if issubclass(kind, list) else tuple
            if sequence.__len__(value) > remaining:
                raise ValueError
            return [snapshot(child, depth + 1) for child in sequence.__iter__(value)]
        if issubclass(kind, str):
            length = str.__len__(value)
            bytes_left -= length
            if bytes_left < 0:
                raise ValueError
            if inline:
                inline_work -= length
                if inline_work < 0:
                    raise _UseWorker
            return value if kind is str else str.__str__(value)
        if kind is bool or value is None:
            return value
        if issubclass(kind, int):
            bits = int.bit_length(value)
            bytes_left -= bits // 4  # Lower bound on decimal digits before any conversion.
            if bits > 14_000 or bytes_left < 0:
                raise ValueError
            if inline:
                inline_work -= bits
                if inline_work < 0:
                    raise _UseWorker
            return value if kind is int else int.__int__(value)
        if issubclass(kind, float):
            return value if kind is float else float.__float__(value)
        raise ValueError

    try:
        # Copy only containers and share immutable leaves. Bound even the
        # temporary copy before measuring its exact wire and retained costs.
        owned = snapshot(params, 1)
    except (ValueError, RuntimeError):
        # Includes a dictionary resized by its caller during traversal.
        return None
    charge = measure(command, owned)
    return None if charge is None else PreparedCommand(owned, charge)


async def prepare_command(
    command: str,
    params: dict[str, Any],
    measure: Callable[[str, dict[str, Any]], int | None],
    *,
    max_bytes: int,
    max_nodes: int,
    max_depth: int,
) -> PreparedCommand | None:
    args = (command, params, measure, max_bytes, max_nodes, max_depth)
    try:
        return _prepare(*args, inline=True)
    except _UseWorker:
        pass
    slots = _workers
    if not slots.acquire(blocking=False):
        raise CommandPreparationBusy
    exited: Future[None] = Future()
    abandoned = Event()
    result: list[PreparedCommand | None] = []

    def run():
        try:
            if not abandoned.is_set():
                result.append(_prepare(*args, inline=False))
        finally:
            exited.set_result(None)

    try:
        worker = submit_owned_executor(asyncio.get_running_loop(), None, run)
    except BaseException:
        slots.release()
        raise

    def completed(future):
        if not future.cancelled():
            future.exception()

    worker.add_done_callback(completed)
    try:
        await asyncio.shield(worker)
        return result.pop()
    finally:
        # Native completion and waiter consumption can be far apart. The executor
        # Future carries no snapshot; the slot owns it until both parties leave.
        abandoned.set()

        def release(_):
            result.clear()
            slots.release()

        exited.add_done_callback(release)
