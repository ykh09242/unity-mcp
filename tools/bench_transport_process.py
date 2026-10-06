"""Bounded process ownership cleanup, including cancellation of its caller."""
from __future__ import annotations

import sys
import sysconfig
from pathlib import Path

from contextlib import suppress
from typing import Protocol

import anyio


def native_python(arguments: list[str], paths: tuple[Path, ...]) -> list[str]:
    """Launch the actual interpreter with only pinned dependency/source paths.

    Windows venv executables may redirect to another PID. -I -S prevents
    implicit user/global sites; the explicit venv site keeps the installed SDK.
    """
    bootstrap = ("import runpy,site,sys;sys.prefix=" + repr(sys.prefix) + ";sys.exec_prefix=" + repr(sys.exec_prefix)
                 + ";site.addsitedir(" + repr(sysconfig.get_path("purelib")) + ");"
                 "sys.path[:0]=" + repr([str(path) for path in paths]) + ";"
                 "sys.argv=sys.argv[1:];runpy.run_path(sys.argv[0],run_name='__main__')")
    return [sys._base_executable, "-I", "-S", "-c", bootstrap, *arguments]


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
