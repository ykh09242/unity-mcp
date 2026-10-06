"""Actual SDK stdio writer backpressure and typed response ownership."""
import asyncio
import gc
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from contextlib import asynccontextmanager

import pytest
from mcp.shared.message import SessionMessage
from mcp.types import ErrorData, JSONRPCError, JSONRPCResponse

from models.response_limits import ResponseOwner, response_size
from services.tools.shared_read_budget import SharedReadBudget
from services.tools.shared_tool_reads import SharedToolReads
from transport.response_limit_middleware import ResponseLimitMiddleware
from transport.stdio_response_delivery import StdioResponseDelivery, retained_stdio_server, stdio_delivery


@pytest.fixture(autouse=True)
def collect_owned_streams():
    """Detect deferred stream-finalizer warnings in their owning test."""
    yield
    gc.collect()


@asynccontextmanager
async def owned_stdio_streams(stdin, stdout):
    # These direct SDK tests replace the protocol dispatcher, which normally
    # owns/closes both consumer endpoints, including on cancellation/failure.
    async with retained_stdio_server(stdin, stdout) as (read, write):
        async with read, write:
            yield read, write


class IdleInput:
    def __init__(self):
        self.stop = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        await self.stop.wait()
        raise StopAsyncIteration


class BlockedOutput:
    def __init__(self, phase):
        self.phase = phase
        self.current = None
        self.entered = {}
        self.resume = {}
        self.lines = []
        self.flushed = []

    def gate(self, request_id):
        key = (type(request_id), request_id)
        return self.entered.setdefault(key, asyncio.Event()), self.resume.setdefault(key, asyncio.Event())

    async def write(self, text):
        self.current = json.loads(text)["id"]
        self.lines.append(text)
        if self.phase == "write":
            entered, resume = self.gate(self.current)
            entered.set()
            await resume.wait()

    async def flush(self):
        if self.phase == "flush":
            entered, resume = self.gate(self.current)
            entered.set()
            await resume.wait()
        self.flushed.append(self.current)


def context(request_id):
    rc = SimpleNamespace(request=None, request_id=str(request_id), _srctx=SimpleNamespace(request_id=request_id))
    return SimpleNamespace(fastmcp_context=SimpleNamespace(request_context=rc))


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["write", "flush"])
async def test_actual_sdk_keeps_source_and_delayed_copy_charged_until_each_flush(monkeypatch, phase):
    import services.tools.shared_tool_reads as shared
    value = {"data": "x" * (1024 * 1024)}
    charge = response_size(value)
    budget = SharedReadBudget(max_bytes=3 * charge)
    monkeypatch.setattr(shared, "shared_read_budget", budget)
    reads = SharedToolReads(freshness_s=5, retention_s=5)
    stdin, stdout = IdleInput(), BlockedOutput(phase)
    guard = ResponseLimitMiddleware()

    async def produce(request_id, write):
        async def call_next(_):
            async with reads.session("owned") as read:
                return await read.fetch(lambda: asyncio.sleep(0, result=value))
        result = await guard.on_message(context(request_id), call_next)
        await write.send(SessionMessage(JSONRPCResponse(jsonrpc="2.0", id=request_id, result=result)))

    async with owned_stdio_streams(stdin, stdout) as (_, write):
        first = asyncio.create_task(produce(7, write))
        entered, resume = stdout.gate(7)
        await asyncio.wait_for(entered.wait(), 3)
        await asyncio.wait_for(first, 3)
        await asyncio.sleep(0)  # producer task_done has run, stdout is still blocked
        assert budget.retained_bytes == 2 * charge
        second = asyncio.create_task(produce("7", write))
        for _ in range(200):
            if budget.retained_bytes == 3 * charge:
                break
            await asyncio.sleep(0.005)
        assert budget.retained_bytes == 3 * charge
        assert len(stdio_delivery.get().pending) == 2
        refused = ResponseOwner()
        assert not budget.reserve(refused, charge)
        resume.set()
        entered_second, resume_second = stdout.gate("7")
        await asyncio.wait_for(entered_second.wait(), 3)
        await asyncio.wait_for(second, 3)
        await asyncio.sleep(0)
        assert budget.retained_bytes == 2 * charge  # source + independently delayed string-ID copy
        assert len(stdio_delivery.get().pending) == 1
        resume_second.set()
        await write.aclose()
        stdin.stop.set()
    await reads.invalidate("owned")
    assert budget.retained_bytes == 0
    assert [json.loads(line)["id"] for line in stdout.lines] == [7, "7"]


@pytest.mark.asyncio
@pytest.mark.parametrize("after_handoff", [False, True])
async def test_cancelled_producer_releases_only_before_handoff_and_disconnect_drains(after_handoff):
    ledger = {"copy": 100}
    stdin, stdout = IdleInput(), BlockedOutput("write")
    entered, _ = stdout.gate("owned")
    acquired, hold, observed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    owner_ref = []

    async def serve():
        async with owned_stdio_streams(stdin, stdout) as (_, write):
            async def producer():
                async def next_call(_):
                    from models.response_limits import response_owner
                    owner = response_owner.get()
                    owner_ref.append(owner)
                    owner.entries.append((ledger, "copy"))
                    acquired.set()
                    if not after_handoff:
                        await hold.wait()
                    return {"data": "owned"}
                result = await ResponseLimitMiddleware().on_message(context("owned"), next_call)
                await write.send(SessionMessage(JSONRPCResponse(jsonrpc="2.0", id="owned", result=result)))
                await hold.wait()  # request task can be canceled after channel acceptance
            task = asyncio.create_task(producer())
            await (entered.wait() if after_handoff else acquired.wait())
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.sleep(0)
            assert bool(ledger) == after_handoff
            observed.set()
            await hold.wait()

    server = asyncio.create_task(serve())
    await asyncio.wait_for(acquired.wait(), 3)
    if after_handoff:
        await asyncio.wait_for(entered.wait(), 3)
    await asyncio.wait_for(observed.wait(), 3)
    for _ in range(100):
        if (owner_ref and owner_ref[0].released) == (not after_handoff):
            break
        await asyncio.sleep(0.005)
    assert bool(ledger) == after_handoff
    server.cancel()  # owned stdio generation/blocked writer disconnect
    with pytest.raises(asyncio.CancelledError):
        await server
    assert ledger == {}
    assert owner_ref[0].released


def test_registry_is_bounded_typed_and_generation_owned():
    delivery = StdioResponseDelivery(max_pending=2)
    first, second = ResponseOwner(), ResponseOwner()
    delivery.register(1, first)
    delivery.register("1", second)
    with pytest.raises(ValueError, match="already active"):
        delivery.register(1, ResponseOwner())
    with pytest.raises(ValueError, match="capacity"):
        delivery.register(2, ResponseOwner())
    replacement = StdioResponseDelivery()
    newer = ResponseOwner()
    replacement.register(1, newer)
    delivery.close()
    assert first.released and second.released
    assert not newer.released
    replacement.close()


@pytest.mark.asyncio
async def test_duplicate_sdk_error_flush_cannot_release_original_active_producer():
    ledger = {"original": 100}
    stdin, stdout = IdleInput(), BlockedOutput("none")
    ready, complete = asyncio.Event(), asyncio.Event()
    guard = ResponseLimitMiddleware()
    async with owned_stdio_streams(stdin, stdout) as (_, write):
        async def first():
            async def next_call(_):
                from models.response_limits import response_owner
                response_owner.get().entries.append((ledger, "original"))
                ready.set()
                await complete.wait()
                return {"original": True}
            result = await guard.on_message(context(7), next_call)
            await write.send(SessionMessage(JSONRPCResponse(jsonrpc="2.0", id=7, result=result)))
        original = asyncio.create_task(first())
        await ready.wait()
        with pytest.raises(ValueError, match="already active"):
            await guard.on_message(context(7), lambda _: asyncio.sleep(0, result={}))
        # The installed dispatcher turns that failure into this same-ID error.
        await write.send(SessionMessage(JSONRPCError(jsonrpc="2.0", id=7,
            error=ErrorData(code=-32603, message="Duplicate request"))))
        async def flushed():
            while not stdout.flushed:
                await asyncio.sleep(0)
        await asyncio.wait_for(flushed(), 3)
        assert not original.done()
        assert ledger == {"original": 100}
        complete.set()
        await original
        await write.aclose()
        stdin.stop.set()
    assert ledger == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["write", "flush"])
async def test_actual_sdk_writer_failure_releases_delivery(phase):
    ledger = {"copy": 100}
    stdin, entered, fail = IdleInput(), asyncio.Event(), asyncio.Event()
    class BrokenOutput:
        async def write(self, text):
            if phase == "write":
                entered.set()
                await fail.wait()
                raise BrokenPipeError("owned reader disconnected")
        async def flush(self):
            if phase == "flush":
                entered.set()
                await fail.wait()
                raise BrokenPipeError("owned reader disconnected")
    with pytest.raises(BaseExceptionGroup):
        async with owned_stdio_streams(stdin, BrokenOutput()) as (_, write):
            async def producer():
                async def next_call(_):
                    from models.response_limits import response_owner
                    response_owner.get().entries.append((ledger, "copy"))
                    return {"data": "owned"}
                result = await ResponseLimitMiddleware().on_message(context(1), next_call)
                await write.send(SessionMessage(JSONRPCResponse(jsonrpc="2.0", id=1, result=result)))
            task = asyncio.create_task(producer())
            await asyncio.wait_for(entered.wait(), 3)
            await task
            await asyncio.sleep(0)
            assert ledger
            fail.set()
            await asyncio.Event().wait()  # SDK task group terminates this owned connection
    assert ledger == {}


def test_unsupported_sdk_stdout_shape_fails_before_claim(monkeypatch):
    import transport.stdio_response_delivery as module
    called = []
    def invalid_claim():
        called.append(True)
    monkeypatch.setattr(module, "sdk_stdio", SimpleNamespace(
        _claim_fd=invalid_claim, _open_stdout_diversion=lambda: None, _UnownedTextWrapper=lambda: None))
    with pytest.raises(RuntimeError, match="unsupported"):
        module._claim_sdk_stdout()
    assert called == []


@pytest.mark.asyncio
async def test_missing_stdio_request_identity_rejects_owned_result_without_fallback():
    ledger = {"copy": 100}
    stdin, stdout = IdleInput(), BlockedOutput("none")
    async with owned_stdio_streams(stdin, stdout) as (_, write):
        async def next_call(_):
            from models.response_limits import response_owner
            response_owner.get().entries.append((ledger, "copy"))
            return {"data": "owned"}
        with pytest.raises(ValueError, match="could not be completed"):
            await ResponseLimitMiddleware().on_message(SimpleNamespace(fastmcp_context=None), next_call)
        assert ledger == {}
        await write.aclose()
        stdin.stop.set()


@pytest.mark.asyncio
async def test_cancel_during_sdk_channel_send_retains_ambiguous_handoff_until_disconnect():
    ledger = {"first": 100, "queued": 100}
    stdin, stdout = IdleInput(), BlockedOutput("write")
    ready = asyncio.Event()
    async def serve():
        async with owned_stdio_streams(stdin, stdout) as (_, write):
            async def produce(request_id):
                async def next_call(_):
                    from models.response_limits import response_owner
                    response_owner.get().entries.append((ledger, request_id))
                    return {"data": request_id}
                result = await ResponseLimitMiddleware().on_message(context(request_id), next_call)
                await write.send(SessionMessage(JSONRPCResponse(jsonrpc="2.0", id=request_id, result=result)))
            first = asyncio.create_task(produce("first"))
            await stdout.gate("first")[0].wait()
            await first
            second = asyncio.create_task(produce("queued"))
            while not stdio_delivery.get().pending.get((str, "queued"), SimpleNamespace(handed_off=False)).handed_off:
                await asyncio.sleep(0)
            assert not second.done()
            second.cancel()
            with pytest.raises(asyncio.CancelledError):
                await second
            await asyncio.sleep(0)
            assert len(ledger) == 2
            ready.set()
            await asyncio.Event().wait()
    server = asyncio.create_task(serve())
    await asyncio.wait_for(ready.wait(), 3)
    server.cancel()
    with pytest.raises(asyncio.CancelledError):
        await server
    assert ledger == {}


@pytest.mark.asyncio
async def test_unity_runner_real_subprocess_preserves_sdk_wire_and_stray_print_diversion(tmp_path):
    src = Path(__file__).resolve().parents[1] / "src"
    program = (
        "import os,sys\n"
        f"sys.path.insert(0,{str(src)!r})\n"
        f"os.environ['UNITY_MCP_LOG_DIR']={str(tmp_path)!r}\n"
        "os.environ['UNITY_MCP_TELEMETRY_ENABLED']='0'\n"
        "from main import UnityMCP\n"
        "from transport.response_limit_middleware import ResponseLimitMiddleware\n"
        "server=UnityMCP('owned-stdio-delivery')\n"
        "server.add_middleware(ResponseLimitMiddleware())\n"
        "@server.tool\n"
        "def owned_echo() -> dict:\n"
        " print('owned stray print stays off wire',flush=True)\n"
        " return {'ok':True}\n"
        "server.run(transport='stdio',show_banner=False)\n"
    )
    process = await asyncio.create_subprocess_exec(sys.executable, "-c", program,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        async def send(message):
            process.stdin.write((json.dumps(message) + "\n").encode())
            await process.stdin.drain()
        await send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "owned", "version": "1"}}})
        initialized = json.loads(await asyncio.wait_for(process.stdout.readline(), 15))
        assert initialized["id"] == 1 and "result" in initialized
        await send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        await send({"jsonrpc": "2.0", "id": "owned", "method": "tools/call", "params": {"name": "owned_echo", "arguments": {}}})
        answer = json.loads(await asyncio.wait_for(process.stdout.readline(), 5))
        assert answer["id"] == "owned" and answer["result"]["structuredContent"] == {"ok": True}
        process.stdin.close()
        await asyncio.wait_for(process.wait(), 8)
        assert process.returncode == 0
        assert await process.stdout.read() == b""
        assert b"owned stray print stays off wire" in await process.stderr.read()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
