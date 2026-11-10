"""MCP stdin admission before SDK graph materialization and handoff."""

import asyncio
import io
import json
import sys
import threading
import weakref
from types import SimpleNamespace

import anyio
import pytest

from mcp.shared.message import SessionMessage
from mcp.shared.jsonrpc_dispatcher import JSONRPCDispatcher

from transport import stdio_response_delivery as delivery
from transport.stdio_input_decode import StdioInputAdmission
from tests.timing import await_signal, watchdog_timeout


class TextInput:
    def __init__(self, *lines):
        self.lines = lines

    async def __aiter__(self):
        for line in self.lines:
            yield line


@pytest.mark.asyncio
async def test_deep_envelope_is_refused_before_sdk_and_next_line_survives(monkeypatch):
    calls = []
    original = delivery.jsonrpc_message_adapter

    def decode(line, **options):
        calls.append(line)
        return original.validate_json(line, **options)

    monkeypatch.setattr(delivery, "jsonrpc_message_adapter", SimpleNamespace(validate_json=decode))
    deep = '{"jsonrpc":"2.0","id":1,"method":"x","params":{"v":' + "[" * 73 + "0" + "]" * 73 + "}}"
    normal = '{"jsonrpc":"2.0","id":2,"method":"ping"}'
    with io.BytesIO() as output:
        async with delivery.retained_stdio_server(
            stdin=TextInput(deep, normal), binary_stdout=output
        ) as (read, write):
            async with read, write:
                rejected = await read.receive()
                accepted = await read.receive()
    assert isinstance(rejected, ValueError)
    assert calls == [normal]
    assert isinstance(accepted, SessionMessage) and accepted.message.id == 2


@pytest.mark.asyncio
async def test_malformed_envelope_error_drops_payload_and_sdk_traceback():
    marker = "PRIVATE-INPUT-" * 1000
    raw = '{"jsonrpc":"invalid","id":1,"method":"' + marker + '"}'
    with io.BytesIO() as output:
        async with delivery.retained_stdio_server(stdin=TextInput(raw), binary_stdout=output) as (
            read,
            write,
        ):
            async with read, write:
                rejected = await read.receive()
    assert isinstance(rejected, ValueError)
    assert len(str(rejected)) < 100
    assert "PRIVATE-INPUT" not in str(rejected)
    assert rejected.__traceback__ is None
    assert rejected.__context__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["depth", "nodes", "work"])
async def test_admission_boundary_precedes_graph_decode(kind):
    line = '{"jsonrpc":"2.0","id":1,"method":"x","params":{"v":[0]}}'
    # Containers and all keys/scalars count: root=1, top-level pairs=7,
    # params object/key/array/value=4; its maximum container depth is three.
    graph = sys.getsizeof(line) + 5 * len(line) + 512 * 12 + 4096
    limits = {"max_depth": 3, "max_nodes": 12, "max_request": graph}
    option = {"depth": "max_depth", "nodes": "max_nodes", "work": "max_request"}[kind]
    calls = []

    def decode(raw, **options):
        calls.append(True)
        return delivery.jsonrpc_message_adapter.validate_json(raw, **options)

    admission = StdioInputAdmission(**limits)
    async with admission.parse(line, decode) as message:
        assert not isinstance(message, Exception)
        assert admission.retained_bytes == graph
    assert admission.retained_bytes == 0 and calls == [True]
    limits[option] -= 1
    admission = StdioInputAdmission(**limits)
    async with admission.parse(line, decode) as message:
        assert isinstance(message, ValueError)
        assert "limits" in str(message)
    assert admission.retained_bytes == 0 and calls == [True]


@pytest.mark.asyncio
async def test_dense_three_megabyte_container_input_is_rejected_before_sdk():
    line = '{"jsonrpc":"2.0","id":1,"method":"x","params":{"v":[' + "[]," * 1_000_129 + "[]]}}"
    assert 3_000_000 < len(line) < 3_100_000
    calls = []
    admission = StdioInputAdmission()
    async with admission.parse(line, lambda *args, **kwargs: calls.append(True)) as message:
        assert isinstance(message, ValueError)
    assert calls == [] and admission.retained_bytes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("line", ['{"unterminated":"secret', '{"x":}', "]}[{", "", "null"])
async def test_malformed_inputs_have_bounded_detached_errors(line):
    admission = StdioInputAdmission()
    async with admission.parse(line, delivery.jsonrpc_message_adapter.validate_json) as message:
        assert isinstance(message, ValueError)
        assert len(str(message)) < 100
        assert message.__traceback__ is message.__context__ is None
        assert admission.retained_bytes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        {"jsonrpc": "2.0", "id": "a", "method": "x", "params": {"v": "한글😀"}},
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}},
        {"jsonrpc": "2.0", "id": 1, "result": {"v": [True, None, 1.5]}},
        {"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "error"}},
        # Preserve the installed adapter's union/extra-field semantics.
        {"jsonrpc": "2.0", "id": [], "method": "x"},
    ],
)
async def test_admitted_message_matches_sdk_without_truncation(value):
    line = json.dumps(value, ensure_ascii=False)
    expected = delivery.jsonrpc_message_adapter.validate_json(line, by_name=False)
    admission = StdioInputAdmission()
    async with admission.parse(line, delivery.jsonrpc_message_adapter.validate_json) as message:
        assert type(message) is type(expected)
        assert message.model_dump() == expected.model_dump()
    assert admission.retained_bytes == 0


@pytest.mark.asyncio
async def test_escaped_widening_workspace_is_reserved_before_sdk():
    line = '{"jsonrpc":"2.0","id":1,"method":"x","params":{"v":"' + "a" * 1000 + '\\ud83d\\ude00"}}'
    calls = []
    admission = StdioInputAdmission(max_request=20_000)
    async with admission.parse(line, lambda *args, **kwargs: calls.append(True)) as message:
        assert isinstance(message, ValueError)
    assert calls == [] and admission.retained_bytes == 0


@pytest.mark.asyncio
async def test_exact_64mib_ascii_envelope_still_passes_sdk_validation():
    prefix = '{"jsonrpc":"2.0","id":1,"method":"x","params":{"v":"'
    suffix = '"}}'
    count = delivery.MAX_STDIO_INPUT_BYTES - len(prefix) - len(suffix)
    line = prefix + "a" * count + suffix + "\n"
    admission = StdioInputAdmission()
    async with admission.parse(line, delivery.jsonrpc_message_adapter.validate_json) as message:
        assert not isinstance(message, Exception)
        assert len(message.params["v"]) == count
        assert admission.retained_bytes <= admission.max_request
    assert admission.retained_bytes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_mode", ["asyncio", "anyio"])
async def test_two_workers_saturate_and_repeated_cancellation_keeps_actual_work_owned(cancel_mode):
    admission = StdioInputAdmission()
    line = '{"jsonrpc":"2.0","id":1,"method":"ping"}'
    loop = asyncio.get_running_loop()
    entered = [asyncio.Event(), asyncio.Event()]
    release = threading.Event()
    guard = threading.Lock()
    calls, results, scopes = [], [], []
    main_thread = threading.get_ident()

    class Result:
        pass

    def decode(*args, **kwargs):
        assert threading.get_ident() != main_thread
        with guard:
            index = len(calls)
            calls.append(index)
        loop.call_soon_threadsafe(entered[index].set)
        assert release.wait(watchdog_timeout())
        result = Result()
        results.append(weakref.ref(result))
        return result

    async def parse():
        with anyio.CancelScope() as scope:
            scopes.append(scope)
            async with admission.parse(line, decode):
                pytest.fail("a cancelled parse was delivered")

    tasks = [asyncio.create_task(parse()) for _ in range(2)]
    queued = None
    try:
        for index, event in enumerate(entered):
            await await_signal(event, f"decoder {index} entered", producers=tasks)
        running_charge = admission.retained_bytes
        queued = asyncio.create_task(parse())
        await asyncio.sleep(0)
        assert admission.retained_bytes > running_charge
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        assert admission.retained_bytes == running_charge and len(calls) == 2
        for index, task in enumerate(tasks):
            if cancel_mode == "asyncio":
                task.cancel()
            else:
                scopes[index].cancel()
        await asyncio.sleep(0)
        for task in tasks:
            task.cancel()
        await asyncio.sleep(0)
        assert not any(task.done() for task in tasks)
        assert admission.retained_bytes == running_charge
    finally:
        release.set()
        await asyncio.gather(*tasks, *([queued] if queued else []), return_exceptions=True)
    assert admission.retained_bytes == 0
    assert len(calls) == 2 and len(results) == 2
    assert all(result() is None for result in results)


@pytest.mark.asyncio
async def test_decoded_message_waiting_for_stream_handoff_keeps_aggregate_charge(monkeypatch):
    admission = StdioInputAdmission()
    monkeypatch.setattr(delivery, "stdio_input_admission", admission)
    parsed = asyncio.Event()
    loop = asyncio.get_running_loop()
    original = delivery.jsonrpc_message_adapter
    calls = []

    def decode(line, **options):
        calls.append(True)
        result = original.validate_json(line, **options)
        loop.call_soon_threadsafe(parsed.set)
        return result

    monkeypatch.setattr(delivery, "jsonrpc_message_adapter", SimpleNamespace(validate_json=decode))
    line = '{"jsonrpc":"2.0","id":1,"method":"ping"}'
    with io.BytesIO() as output:
        async with delivery.retained_stdio_server(stdin=TextInput(line), binary_stdout=output) as (
            read,
            write,
        ):
            async with read, write:
                await await_signal(parsed, "decoded input")
                charge = admission.retained_bytes
                assert charge > 0
                admission.max_total = charge
                async with admission.parse(line, decode) as rejected:
                    assert isinstance(rejected, ValueError)
                    assert "capacity" in str(rejected)
                assert admission.retained_bytes == charge and calls == [True]
                await read.receive()
        assert admission.retained_bytes == 0


@pytest.mark.asyncio
async def test_cancelling_queued_stream_delivery_releases_its_graph(monkeypatch):
    admission = StdioInputAdmission()
    monkeypatch.setattr(delivery, "stdio_input_admission", admission)
    parsed = asyncio.Event()
    loop = asyncio.get_running_loop()
    original = delivery.jsonrpc_message_adapter
    graphs = []
    streams = []

    def decode(line, **options):
        result = original.validate_json(line, **options)
        graphs.append(weakref.ref(result))
        loop.call_soon_threadsafe(parsed.set)
        return result

    monkeypatch.setattr(delivery, "jsonrpc_message_adapter", SimpleNamespace(validate_json=decode))

    async def serve():
        with io.BytesIO() as output:
            async with delivery.retained_stdio_server(
                stdin=TextInput('{"jsonrpc":"2.0","id":1,"method":"ping"}'),
                binary_stdout=output,
            ) as (read, _write):
                streams.append(read)
                await asyncio.Event().wait()

    task = asyncio.create_task(serve())
    try:
        await await_signal(parsed, "decoded queued input", producers=(task,))
        async with asyncio.timeout(watchdog_timeout()):
            while streams[0]._inner.statistics().tasks_waiting_send != 1:
                await asyncio.sleep(0)
        assert admission.retained_bytes > 0
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert admission.retained_bytes == 0
    assert len(graphs) == 1 and graphs[0]() is None


@pytest.mark.asyncio
async def test_sdk_request_burst_reuses_decoder_budget_while_handlers_are_running(monkeypatch):
    # This budget deliberately covers decode/queued delivery, not the SDK's
    # independently spawned handler tasks. Keep that scope executable and honest.
    admission = StdioInputAdmission(max_total=32_768)
    monkeypatch.setattr(delivery, "stdio_input_admission", admission)
    count = 32
    handlers_ready, consumed, release, stop, flushed = (asyncio.Event() for _ in range(5))
    seen, errors = [], []
    loop = asyncio.get_running_loop()

    class Input:
        async def __aiter__(self):
            yield '{"jsonrpc":"bad","method":"private-marker"}'
            for index in range(count):
                yield json.dumps(
                    {"jsonrpc": "2.0", "id": index, "method": "x", "params": {"v": index}}
                )
            consumed.set()
            await stop.wait()

    class Output(io.BytesIO):
        writes = 0

        def flush(self):
            self.writes += 1
            if self.writes == count:
                loop.call_soon_threadsafe(flushed.set)

    async def request(_context, method, params):
        assert method == "x"
        seen.append(params["v"])
        if len(seen) == count:
            handlers_ready.set()
        await release.wait()
        return {"value": params["v"]}

    async def notify(*_args):
        pytest.fail("no notification was sent")

    async def exception(error):
        errors.append(error)

    with Output() as output:
        async with delivery.retained_stdio_server(stdin=Input(), binary_stdout=output) as (
            read,
            write,
        ):
            dispatcher = JSONRPCDispatcher(read, write, on_stream_exception=exception)
            task = asyncio.create_task(dispatcher.run(request, notify))
            try:
                await await_signal(handlers_ready, "all SDK handlers entered", producers=(task,))
                await await_signal(consumed, "all input handed off", producers=(task,))
                assert admission.retained_bytes == 0
                assert seen == list(range(count))
                assert len(errors) == 1 and str(errors[0]) == "Invalid MCP stdio message"
                release.set()
                await await_signal(flushed, "all responses flushed", producers=(task,))
                stop.set()
                await task
            finally:
                release.set()
                stop.set()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert {item["id"]: item["result"] for item in responses} == {
        index: {"value": index} for index in range(count)
    }
