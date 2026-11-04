"""Bounded stdio ingress before decoding or protocol dispatch."""

from tests.timing import await_signal, watchdog_timeout

import asyncio
import io
import os
import threading
import json
from textwrap import dedent
from types import SimpleNamespace

import pytest

import transport.stdio_response_delivery as module
from .stdio_process import owned_sdk_process


class FragmentedBinary:
    def __init__(self, data, chunk=3):
        self.data, self.offset, self.chunk = data, 0, chunk
        self.requests = []

    def read1(self, size):
        self.requests.append(size)
        size = min(size, self.chunk, len(self.data) - self.offset)
        result = self.data[self.offset : self.offset + size]
        self.offset += size
        return result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "wire",
    [
        b"one\ntwo\r\nthree\rfour",
        "\ud55c\uae00😀\r\nlast".encode(),
        b"bad\xff\npartial\xe3\x81",
        b"\r\n\n\rlast\r",
        b"",
    ],
)
@pytest.mark.parametrize("chunk", [1, 3, 65_536])
async def test_binary_fragments_match_sdk_replacement_universal_newlines_and_eof(wire, chunk):
    # Given the same bytes and text settings as the pinned SDK input wrapper.
    with io.TextIOWrapper(io.BytesIO(wire), encoding="utf-8", errors="replace") as baseline:
        expected = list(baseline)
    source = FragmentedBinary(wire, chunk)
    # When a bounded binary input receives arbitrarily fragmented data.
    actual = [line async for line in module._BoundedStdioInput(source, max_bytes=64)]
    # Then UTF-8, CRLF/bare CR, partial final lines and EOF match the SDK.
    assert actual == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", [b"\n", b"\r\n", b"\r", b""])
async def test_binary_exact_payload_limit_accepts_delimiter_or_eof(ending):
    source = FragmentedBinary(b"x" * 16 + ending, 3)
    lines = [line async for line in module._BoundedStdioInput(source, max_bytes=16)]
    assert lines == ["x" * 16 + ("\n" if ending else "")]


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", [b"\n", b"\r\n", b""])
async def test_binary_oversize_stops_without_unbounded_read_or_drain(ending):
    source = FragmentedBinary(b"x" * 100_000 + ending, 100_000)
    with pytest.raises(ValueError, match="size limit"):
        async for _ in module._BoundedStdioInput(source, max_bytes=16):
            pytest.fail("oversize input was delivered")
    assert source.offset <= 17
    assert max(source.requests) <= 17


@pytest.mark.asyncio
async def test_utf8_limit_counts_wire_bytes_not_decoded_characters():
    source = FragmentedBinary("\ud55c\uae00😀".encode() + b"\n", 1)
    with pytest.raises(ValueError, match="size limit"):
        async for _ in module._BoundedStdioInput(source, max_bytes=9):
            pytest.fail("10-byte Unicode payload exceeded its allowance")


@pytest.mark.asyncio
async def test_explicit_text_input_is_rejected_before_protocol_parser(monkeypatch):
    class TextInput:
        def __aiter__(self):
            return self.lines()

        async def lines(self):
            yield '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n'

    class ForbiddenParser:
        def validate_json(self, *args, **kwargs):
            pytest.fail("oversize text reached the JSON parser")

    monkeypatch.setattr(module, "jsonrpc_message_adapter", ForbiddenParser())
    with io.BytesIO() as output:
        with pytest.raises(BaseExceptionGroup) as error:
            async with module.retained_stdio_server(
                stdin=TextInput(), binary_stdout=output, max_input_bytes=8
            ) as (read, write):
                async with read, write:
                    await read.receive()
        assert any(isinstance(exc, module._StdioInputTooLarge) for exc in error.value.exceptions)


@pytest.mark.asyncio
async def test_cancelled_input_waits_for_owned_descriptor_read_to_settle():
    entered, finished, resume = threading.Event(), threading.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    observed = asyncio.Event()

    class Blocked(FragmentedBinary):
        def read1(self, size):
            entered.set()
            loop.call_soon_threadsafe(observed.set)
            if not resume.wait(watchdog_timeout()):
                raise TimeoutError("owned input gate was not released")
            result = super().read1(size)
            finished.set()
            return result

    reader = module._BoundedStdioInput(Blocked(b"line\n", 10), max_bytes=8)
    task = asyncio.create_task(reader.__anext__())
    try:
        await await_signal(observed, "observed")
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert not finished.is_set()
        resume.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, watchdog_timeout())
        assert finished.is_set()
    finally:
        resume.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("binary_output", [False, True])
async def test_default_owned_binary_input_rejects_before_parser_and_restores_claim(
    monkeypatch, binary_output
):
    source = FragmentedBinary(b"x" * 1000, 1000)
    restored = []
    monkeypatch.setattr(
        module,
        "_claim_sdk_stdin",
        lambda limit: (
            module._BoundedStdioInput(source, max_bytes=limit),
            lambda: restored.append(True),
        ),
    )

    class ForbiddenParser:
        def validate_json(self, *args, **kwargs):
            pytest.fail("oversize bytes reached the JSON parser")

    monkeypatch.setattr(module, "jsonrpc_message_adapter", ForbiddenParser())

    class TextOutput:
        async def write(self, text):
            pytest.fail("no protocol response should be produced")

        async def flush(self):
            pass

    with io.BytesIO() as output:
        options = {"binary_stdout": output} if binary_output else {"stdout": TextOutput()}
        with pytest.raises(BaseExceptionGroup) as error:
            async with module.retained_stdio_server(max_input_bytes=16, **options) as (read, write):
                async with read, write:
                    await read.receive()
        assert any(isinstance(exc, module._StdioInputTooLarge) for exc in error.value.exceptions)
    assert source.offset <= 17
    assert restored == [True]


class RepeatingBinary:
    """Generate large input lazily; no whole input fixture is retained."""

    def __init__(self, payload_bytes, ending):
        self.remaining, self.ending = payload_bytes, ending
        self.received = 0
        self.requests = []

    def read1(self, size):
        self.requests.append(size)
        count = min(size, self.remaining)
        if count:
            self.remaining -= count
            self.received += count
            return b"x" * count
        result, self.ending = self.ending[:size], self.ending[size:]
        return result


@pytest.mark.asyncio
@pytest.mark.parametrize("oversize", [False, True])
async def test_default_64mib_boundary_is_enforced_before_decode(oversize):
    payload_bytes = 64 * 1024 * 1024
    source = RepeatingBinary(payload_bytes + int(oversize), b"\n")
    reader = module._BoundedStdioInput(source)
    if oversize:
        with pytest.raises(module._StdioInputTooLarge):
            await reader.__anext__()
        assert source.received == payload_bytes + 1
    else:
        line = await reader.__anext__()
        assert len(line) == payload_bytes + 1 and line.endswith("\n")
    assert max(source.requests) <= 65_536


@pytest.mark.asyncio
async def test_explicit_text_multibyte_limit_accepts_exact_line_and_rejects_next():
    class Text:
        async def __aiter__(self):
            yield "\ud55c\uae00\r\n"
            yield "\ud55c\uae00😀\n"

    reader = module._CheckedTextInput(Text(), 6).__aiter__()
    assert await reader.__anext__() == "\ud55c\uae00\r\n"
    with pytest.raises(module._StdioInputTooLarge):
        await reader.__anext__()


@pytest.mark.asyncio
async def test_real_sdk_short_roundtrip_open_stdin_then_no_newline_oversize_and_fd_restore(
    tmp_path,
):
    program = (
        "import asyncio,sys,os\n"
        "print('owned pid:'+str(os.getpid()),file=sys.stderr,flush=True)\n"
        "from mcp.server.lowlevel.server import Server\n"
        "from transport.stdio_response_delivery import retained_stdio_server\n"
        'server=Server("owned-bounded-input")\n'
        "async def run():\n"
        " try:\n"
        "  async with retained_stdio_server(max_input_bytes=512) as (read,write):\n"
        "   print('owned diverted stdout',flush=True)\n"
        "   await server.run(read,write,server.create_initialization_options())\n"
        " except BaseExceptionGroup as error:\n"
        "  if 'size limit' not in str(error):\n"
        "   def contains(group):\n"
        "    return any('size limit' in str(e) or isinstance(e,BaseExceptionGroup) and contains(e) for e in group.exceptions)\n"
        "   assert contains(error),repr(error)\n"
        "  print('owned bound rejected',file=sys.stderr,flush=True)\n"
        " print('owned stdout restored',flush=True)\n"
        " tail=sys.stdin.buffer.read(1)\n"
        " print('owned stdin restored:'+tail.decode(),flush=True)\n"
        "asyncio.run(run())\n"
    )
    async with owned_sdk_process(program, tmp_path) as process:
        pid_line = await asyncio.wait_for(process.stderr.readline(), watchdog_timeout())
        assert int(pid_line.removeprefix(b"owned pid:")) == process.pid
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "owned", "version": "1"},
            },
        }
        process.stdin.write(json.dumps(initialize).encode() + b"\n")
        await process.stdin.drain()
        # The client keeps stdin open while waiting: a filling read would hang.
        initialized = json.loads(
            await asyncio.wait_for(process.stdout.readline(), watchdog_timeout())
        )
        assert initialized["id"] == 1 and "result" in initialized
        process.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        process.stdin.write(b"x" * 513)  # no LF: reject at the byte ceiling, not EOF
        await process.stdin.drain()
        restored = await asyncio.wait_for(process.stdout.readline(), watchdog_timeout())
        assert restored.rstrip() == b"owned stdout restored"
        process.stdin.write(b"Z")
        await process.stdin.drain()
        restored_input = await asyncio.wait_for(process.stdout.readline(), watchdog_timeout())
        assert restored_input.rstrip() == b"owned stdin restored:Z"
        process.stdin.close()
        await asyncio.wait_for(process.wait(), watchdog_timeout())
        assert process.returncode == 0
        errors = await process.stderr.read()
        assert b"owned diverted stdout" in errors and b"owned bound rejected" in errors


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("cancel", [False, True])
async def test_owned_stdin_restores_wire_while_parent_writer_stays_open(tmp_path, partial, cancel):
    # Instrument the real SDK claim in the child. Signal when its worker is waiting
    # for the first bytes, or has consumed a partial frame and needs more.
    program = (
        dedent("""
        import asyncio, os, sys
        from transport import stdio_response_delivery as transport

        async def main():
            waiting = asyncio.Event()
            loop = asyncio.get_running_loop()
            claim = transport.sdk_stdio._claim_fd
            releases = []
            reading = False
            original_readline = transport._BoundedStdioInput._readline
            def observe_start(reader):
                if os.name != 'nt' and not PARTIAL:
                    loop.call_soon_threadsafe(waiting.set)
                return original_readline(reader)
            transport._BoundedStdioInput._readline = observe_start
            def observe_claim(fd, stream, mode, open_diversion):
                buffer, release = claim(fd, stream, mode, open_diversion)
                def release_once():
                    assert not reading
                    releases.append(fd)
                    release()
                if fd != 0:
                    return buffer, release_once
                class Observed:
                    partial = False
                    def fileno(self):
                        return buffer.fileno()
                    def read1(self, size):
                        nonlocal reading
                        reading = True
                        try:
                            if os.name == 'nt' and self.partial == PARTIAL:
                                loop.call_soon_threadsafe(waiting.set)
                            chunk = buffer.read1(size)
                            self.partial |= bool(chunk)
                            if os.name != 'nt' and chunk and PARTIAL:
                                loop.call_soon_threadsafe(waiting.set)
                            return chunk
                        finally:
                            reading = False
                return Observed(), release_once
            transport.sdk_stdio._claim_fd = observe_claim
            async def serve():
                async with transport.retained_stdio_server():
                    await (asyncio.Event() if CANCEL else waiting).wait()
            task = asyncio.create_task(serve())
            await waiting.wait()
            if CANCEL:
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                assert CANCEL
            assert releases == [1, 0]
            print('restored', flush=True)
            assert os.read(0, 1) == b'Z'
        asyncio.run(main())
    """)
        .replace("PARTIAL", str(partial))
        .replace("CANCEL", str(cancel))
    )
    async with owned_sdk_process(program, tmp_path) as process:
        if partial:
            process.stdin.write(b'{"partial"')
            await process.stdin.drain()
        restored = await asyncio.wait_for(process.stdout.readline(), watchdog_timeout())
        assert restored.rstrip() == b"restored"
        process.stdin.write(b"Z")
        await process.stdin.drain()
        assert await asyncio.wait_for(process.wait(), watchdog_timeout()) == 0


@pytest.mark.asyncio
async def test_sdk_in_place_input_delivers_prefetched_line_with_writer_still_open(monkeypatch):
    read_fd, write_fd = os.pipe()
    with os.fdopen(read_fd, "r", encoding="utf-8") as source, os.fdopen(write_fd, "wb") as writer:
        writer.write(b"_prefetched\r\n")
        writer.flush()
        assert source.buffer.read(1) == b"_"
        monkeypatch.setattr(module.sys, "stdin", source)
        reader, restore = module._claim_sdk_stdin()
        assert restore is None
        line = await asyncio.wait_for(reader.__anext__(), watchdog_timeout())
        assert line == "prefetched\n"
        if os.name == "posix":
            assert os.get_blocking(read_fd)


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", [False, True])
async def test_redirected_file_and_null_stdin_reach_eof(tmp_path, monkeypatch, empty):
    path = tmp_path / "stdin"
    path.write_bytes("한글".encode())
    with open(os.devnull if empty else path, encoding="utf-8") as source:
        monkeypatch.setattr(module.sys, "stdin", source)
        reader, restore = module._claim_sdk_stdin()
        assert restore is None
        assert [line async for line in reader] == ([] if empty else ["한글"])


@pytest.mark.asyncio
async def test_owned_sdk_timeout_closes_stdin_and_reaps_actual_native_child(tmp_path):
    program = "import os,sys\nprint(os.getpid(),flush=True)\nsys.stdin.buffer.read()\n"
    with pytest.raises(TimeoutError):
        async with owned_sdk_process(program, tmp_path) as process:
            executing_pid = int(
                await asyncio.wait_for(process.stdout.readline(), watchdog_timeout())
            )
            assert executing_pid == process.pid
            assert process.returncode is None
            await asyncio.wait_for(process.stdout.readline(), 0.05)
    assert process.returncode is not None
    assert process.stdin.is_closing()


def test_unsupported_sdk_binary_input_restores_claim_before_rejecting(monkeypatch):
    restored = []

    def claim(fd, stream, mode, open_diversion):
        return SimpleNamespace(), lambda: restored.append(True)

    monkeypatch.setattr(
        module,
        "sdk_stdio",
        SimpleNamespace(
            _claim_fd=claim, _open_stdin_diversion=lambda: None, _UnownedTextWrapper=lambda: None
        ),
    )
    with pytest.raises(RuntimeError, match="binary input API is unsupported"):
        module._claim_sdk_stdin()
    assert restored == [True]
