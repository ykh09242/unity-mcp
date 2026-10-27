"""Bounded cleanup for child processes owned by developer/test harnesses.

Start draining pipes immediately, keep the drain alive across execution timeout,
and give kill/reap/drain a separate budget. No foreign process is acquired here.
"""

import asyncio
from asyncio.subprocess import Process
from contextlib import asynccontextmanager
import math
from collections.abc import AsyncIterator


class ProcessCleanupTimeout(TimeoutError):
    """An owned child or its pipes did not finish within the cleanup budget."""


def positive_seconds(value: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("time budget must be finite and positive")
    return seconds


async def _finish_owned_process(process, output, timeout: float) -> None:
    kill_error = None
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass  # The child exited between the returncode check and kill.
        except OSError as error:
            kill_error = error
    reaped = asyncio.create_task(process.wait(), name="owned-child-reap")
    tasks = {output, reaped}
    done, pending = await asyncio.wait(tasks, timeout=timeout / 2)
    if not pending and not output.cancelled() and kill_error is None:
        output.exception()  # Retrieve errors; the operation sees them separately.
        reaped.result()
        return
    # asyncio Process has no public close-pipes API. This owned transport is the
    # final fallback for descendants holding pipe handles or cancelled drains.
    # Closing it releases pipe readers and requests termination of this child.
    try:
        process._transport.close()
    except OSError as error:
        kill_error = error
    for task in pending:
        task.cancel()
    done, pending = await asyncio.wait(tasks, timeout=timeout / 2)
    for task in done:
        if not task.cancelled():
            task.exception()
    if pending:
        raise ProcessCleanupTimeout(
            "owned child pipe drain/reap did not settle after transport close"
        )
    if kill_error is not None:
        raise kill_error
    raise ProcessCleanupTimeout("owned child exit/drain exceeded its cleanup budget")


@asynccontextmanager
async def drain_owned_process(
    process: Process, cleanup_timeout: float = 10.0
) -> AsyncIterator[asyncio.Task[tuple[bytes, bytes]]]:
    """Yield the sole pipe drain; callers must shield it during operation waits."""
    positive_seconds(str(cleanup_timeout))
    output = asyncio.create_task(process.communicate(), name="owned-child-pipe-drain")
    failure: BaseException | None = None
    try:
        yield output
    except BaseException as error:  # preserve cancellation and the original operation error
        failure = error
        raise
    finally:
        cleanup = asyncio.create_task(
            _finish_owned_process(process, output, cleanup_timeout), name="owned-child-cleanup"
        )
        cancelled = None
        while True:
            try:
                # Repeated cancellation of the operation must not cancel its
                # independently bounded kill/reap/drain owner.
                await asyncio.shield(cleanup)
                break
            except asyncio.CancelledError as error:
                if cleanup.cancelled():
                    raise
                if failure is None:
                    failure = error
                    cancelled = error
            except (OSError, ProcessCleanupTimeout) as error:
                if failure is None:
                    raise
                failure.add_note(f"Owned child cleanup failed: {error}")
                break
        if cancelled is not None:
            raise cancelled
