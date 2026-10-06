"""Bounded process ownership cleanup, including cancellation of its caller."""
from __future__ import annotations

from contextlib import suppress
from typing import Protocol

import anyio


class ProcessLease(Protocol):
    @property
    def returncode(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    async def wait(self) -> int: ...
    async def aclose(self) -> None: ...


async def cleanup_process(process: ProcessLease, grace_seconds: float = 3) -> None:
    """Reap/close even exited children; shield slow-child escalation from cancellation."""
    with anyio.CancelScope(shield=True):
        try:
            if process.returncode is None:
                # The child can exit between observing returncode and signalling.
                with suppress(ProcessLookupError):
                    process.terminate()
            with anyio.move_on_after(grace_seconds) as graceful:
                await process.wait()
            if graceful.cancel_called:
                with suppress(ProcessLookupError):
                    process.kill()
                with anyio.fail_after(3):
                    await process.wait()
        finally:
            with anyio.fail_after(3):
                await process.aclose()
