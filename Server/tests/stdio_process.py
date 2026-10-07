"""Own the executing SDK subprocess PID, bypassing Windows venv redirectors."""

import asyncio
import sys
from asyncio.subprocess import Process
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Final

REPO: Final = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
from bench_transport_process import native_python


@asynccontextmanager
async def owned_sdk_process(program: str, directory: Path) -> AsyncIterator[Process]:
    """Launch pinned SDK dependencies and close/kill/reap the actual interpreter."""
    script = directory / "owned_sdk_stdio.py"
    script.write_text(program, encoding="utf-8")
    command = native_python([str(script)], (REPO / "Server/src",))
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=directory,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        yield process
    finally:
        process.stdin.close()
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
        await asyncio.wait_for(process.wait(), 5)
        with suppress(BrokenPipeError, ConnectionResetError):
            await asyncio.wait_for(process.stdin.wait_closed(), 5)
