"""Owned real pipes must settle blocked output before releasing their responses."""

import asyncio
import os
from textwrap import dedent

import pytest
import anyio

from tests.stdio_process import owned_sdk_process
from tests.timing import watchdog_timeout


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["deadline", "cancel"])
async def test_owned_output_settles_while_all_default_worker_tokens_are_held(monkeypatch, mode):
    import transport.stdio_response_delivery as module
    from transport.stdio_output_write import StdioOutputWrite

    default = anyio.to_thread.current_default_thread_limiter()
    borrowers = [object() for _ in range(default.total_tokens)]
    for borrower in borrowers:
        await default.acquire_on_behalf_of(borrower)
    read_fd, write_fd = os.pipe()
    source = os.fdopen(write_fd, "wb", buffering=0)
    output = StdioOutputWrite.for_pipe(source)
    monkeypatch.setattr(module, "STDIO_WRITE_TIMEOUT", 0.05)
    task = asyncio.create_task(module._settled_binary_write(output, b"x" * (1024 * 1024)))
    try:
        await asyncio.sleep(0)
        if mode == "cancel":
            task.cancel()
        done, _ = await asyncio.wait((task,), timeout=watchdog_timeout(2))
        assert task in done, "owned output waited for unrelated default worker tokens"
        assert default.borrowed_tokens == len(borrowers)
        with pytest.raises(TimeoutError if mode == "deadline" else asyncio.CancelledError):
            task.result()
        assert output._thread_id is None and not source.closed
    finally:
        # A RED run can still have a job queued on the default limiter. Release
        # only our tokens, then await its stopped holder before closing our pipe.
        for borrower in borrowers:
            default.release_on_behalf_of(borrower)
        if not task.done():
            task.cancel()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), watchdog_timeout())
        source.close()
        os.close(read_fd)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cancel", "repeat_cancel", "deadline"])
async def test_blocked_default_stdout_settles_without_draining_reader(tmp_path, mode):
    program = dedent("""
        import asyncio, sys
        from mcp.shared.message import SessionMessage
        from mcp.types import JSONRPCResponse
        from models.response_limits import ResponseOwner
        import transport.stdio_response_delivery as module

        class Input:
            async def __aiter__(self):
                await asyncio.Event().wait()
                yield ""

        async def main():
            owner = ResponseOwner()
            active = False
            entered = asyncio.Event()
            loop = asyncio.get_running_loop()
            original = module._BinaryWriteOperation.run
            def observed(operation):
                nonlocal active
                active = True
                loop.call_soon_threadsafe(entered.set)
                print("writing", file=sys.stderr, flush=True)
                try:
                    return original(operation)
                finally:
                    active = False
            module._BinaryWriteOperation.run = observed
            module.STDIO_WRITE_TIMEOUT = 0.3
            def released():
                assert not active, "response released while writer still owns payload"
            owner.on_release.append(released)
            async def serve():
                async with module.retained_stdio_server(stdin=Input()) as (read, write):
                    module.stdio_delivery.get().register(7, owner)
                    await write.send(SessionMessage(JSONRPCResponse(
                        jsonrpc="2.0", id=7, result={"text":"x" * (2 * 1024 * 1024)}
                    )))
                    await asyncio.Event().wait()
            task = asyncio.create_task(serve())
            await entered.wait()
            await asyncio.sleep(0.1)
            assert active and not owner.released
            if MODE != "deadline":
                task.cancel()
                if MODE == "repeat_cancel":
                    await asyncio.sleep(0.02)
                    task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                assert MODE != "deadline"
            except BaseExceptionGroup as exc:
                assert MODE == "deadline" and exc.subgroup(TimeoutError) is not None
            assert owner.released and not active
            print("settled", file=sys.stderr, flush=True)
        asyncio.run(main())
    """).replace("MODE", repr(mode))
    async with owned_sdk_process(program, tmp_path) as process:
        assert (
            await asyncio.wait_for(process.stderr.readline(), watchdog_timeout())
        ).strip() == b"writing"
        # No stdout consumer: the OS pipe and asyncio's bounded read buffer fill.
        assert (
            await asyncio.wait_for(process.stderr.readline(), watchdog_timeout())
        ).strip() == b"settled"
        output, error = await asyncio.wait_for(process.communicate(), watchdog_timeout())
        assert process.returncode == 0, error.decode(errors="replace")
        expected = (
            b'{"jsonrpc":"2.0","id":7,"result":{"text":"' + b"x" * (2 * 1024 * 1024) + b'"}}\n'
        )
        assert 0 < len(output) < len(expected)
        assert expected.startswith(output)  # Partial output is never replayed from offset zero.


@pytest.mark.asyncio
async def test_default_pipe_writes_large_unicode_frame_exactly(tmp_path):
    program = dedent("""
        import asyncio
        from mcp.shared.message import SessionMessage
        from mcp.types import JSONRPCResponse
        from transport.stdio_response_delivery import retained_stdio_server
        class Empty:
            async def __aiter__(self):
                if False:
                    yield ""
        async def main():
            async with retained_stdio_server(stdin=Empty()) as (read, write):
                await write.send(SessionMessage(JSONRPCResponse(
                    jsonrpc="2.0", id=7, result={"text":"\\U0001f600" * 40000}
                )))
                await write.aclose()
        asyncio.run(main())
    """)
    async with owned_sdk_process(program, tmp_path) as process:
        output, error = await asyncio.wait_for(process.communicate(), watchdog_timeout())
        assert process.returncode == 0, error.decode(errors="replace")
        assert (
            output
            == b'{"jsonrpc":"2.0","id":7,"result":{"text":"' + "😀".encode() * 40000 + b'"}}\n'
        )


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX nonblocking pipe flags and short writes")
async def test_posix_pipe_short_writes_preserve_offsets_and_restore_flags(monkeypatch):
    from transport.stdio_output_write import StdioOutputWrite
    from transport.stdio_response_delivery import _settled_binary_write

    read_fd, write_fd = os.pipe()
    original = os.write
    payload = "한글😀".encode() * 100
    try:
        with os.fdopen(write_fd, "wb", buffering=0) as source:
            output = StdioOutputWrite.for_pipe(source)

            def short_write(fd, data):
                return original(fd, data[:3] if fd == write_fd else data)

            monkeypatch.setattr(os, "write", short_write)
            await _settled_binary_write(output, payload)
            assert os.get_blocking(write_fd) is True
            assert os.read(read_fd, len(payload) + 1) == payload + b"\n"
            assert not source.closed
    finally:
        os.close(read_fd)
