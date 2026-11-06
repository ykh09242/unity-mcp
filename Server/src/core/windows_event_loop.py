"""Fail an owned Windows server run when its selector wakeup socket is broken."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


async def run_with_wakeup_guard(run: Callable[[], Awaitable[None]]) -> None:
    loop = asyncio.get_running_loop()
    owner = asyncio.current_task()
    previous_handler = loop.get_exception_handler()
    failure: OSError | None = None

    def handle_exception(current_loop, context):
        nonlocal failure
        error = context.get("exception")
        callback = getattr(context.get("handle"), "_callback", None)
        wakeup_reader = getattr(current_loop, "_read_from_self", None)
        if isinstance(error, OSError) and wakeup_reader is not None and callback == wakeup_reader:
            # CPython's SelectorEventLoop leaves this reader registered after a
            # reset, so every select() repeats the exception. Recreate only the
            # internal socketpair before cancellation: removing it alone breaks
            # Windows select() with no sockets and loses worker-thread wakeups.
            # These private hooks are shared by supported CPython selector loops.
            current_loop._close_self_pipe()
            current_loop._make_self_pipe()
            if failure is None:
                failure = error
                owner.cancel()
            return
        if previous_handler is not None:
            previous_handler(current_loop, context)
        else:
            current_loop.default_exception_handler(context)

    loop.set_exception_handler(handle_exception)
    try:
        try:
            await run()
        except asyncio.CancelledError:
            if failure is None:
                raise
        if failure is not None:
            raise RuntimeError(
                "Windows event loop wakeup socket failed; the server has stopped. Restart it."
            ) from failure
    finally:
        loop.set_exception_handler(previous_handler)
