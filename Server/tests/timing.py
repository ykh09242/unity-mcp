"""Completion watchdogs for tests, never product or performance deadlines.

Set UNITY_MCP_TEST_TIMEOUT_SCALE for slower/oversubscribed runners. Successful
waits complete immediately; the multiplier adds no sleep or retry. A watchdog
failure leaves the observed operation intact for assertions and owned teardown.
"""

import asyncio
from collections.abc import Iterable
import math
import os
from typing import TypeVar

T = TypeVar("T")
TIMEOUT_SCALE_ENV = "UNITY_MCP_TEST_TIMEOUT_SCALE"


def watchdog_timeout(default_seconds: float = 60.0) -> float:
    """Return an operator-adjustable finite hang bound, rejecting bad settings."""
    try:
        scale = float(os.environ.get(TIMEOUT_SCALE_ENV, "1"))
        result = default_seconds * scale
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{TIMEOUT_SCALE_ENV} must be a positive finite number") from error
    if not math.isfinite(scale) or scale <= 0 or not math.isfinite(result) or result <= 0:
        raise ValueError(f"{TIMEOUT_SCALE_ENV} must produce a positive finite watchdog")
    return result


async def await_completion(
    future: asyncio.Future[T],
    phase: str,
    *,
    timeout: float | None = None,
    producers: Iterable[asyncio.Future] = (),
) -> T:
    """Observe a phase or report an early producer exit without cancelling either.

    Phase completion wins when both are ready. Otherwise producer errors and
    cancellation propagate; successful exit before the phase is an assertion.
    """
    seconds = watchdog_timeout() if timeout is None else timeout
    producers = tuple(producers)
    done, _ = await asyncio.wait(
        {future, *producers}, timeout=seconds, return_when=asyncio.FIRST_COMPLETED
    )
    if future in done:
        return future.result()
    results = [producer.result() for producer in producers if producer in done]
    assert not results, f"Producer completed before {phase}: {results!r}"
    assert future in done, (
        f"Timed out waiting for {phase} after {seconds:g}s; "
        f"increase {TIMEOUT_SCALE_ENV} on slow runners. The operation was not cancelled."
    )
    return future.result()


async def await_signal(
    event: asyncio.Event,
    phase: str,
    *,
    timeout: float | None = None,
    producers: Iterable[asyncio.Future] = (),
) -> None:
    """Wait for a phase event and always release this helper's own waiter."""
    waiter = asyncio.create_task(event.wait())
    try:
        await await_completion(waiter, phase, timeout=timeout, producers=producers)
    finally:
        if not waiter.done():
            waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
