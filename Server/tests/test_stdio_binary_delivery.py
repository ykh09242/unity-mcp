"""Binary stdio serialization and delivery ownership on explicit owned streams."""
import asyncio
import io
import threading
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from contextvars import ContextVar

import pytest
from mcp.shared.message import SessionMessage
from mcp.types import ErrorData, JSONRPCError, JSONRPCNotification, JSONRPCRequest, JSONRPCResponse
from pydantic import BaseModel, Field, ConfigDict

from models.response_limits import ResponseOwner
from transport.stdio_response_delivery import retained_stdio_server, stdio_delivery


class IdleInput:
    def __init__(self):
        self.stop = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        await self.stop.wait()
        raise StopAsyncIteration


class BinaryOutput(io.BytesIO):
    def __init__(self, loop):
        super().__init__()
        self.loop = loop
        self.entered = asyncio.Event()
        self.resume = threading.Event()
        self.threads = []

    def write(self, data):
        self.threads.append(threading.get_ident())
        return super().write(data)

    def flush(self):
        self.threads.append(threading.get_ident())
        self.loop.call_soon_threadsafe(self.entered.set)
        if not self.resume.wait(5):
            raise TimeoutError('owned flush gate was not released')


class AliasModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra='allow')
    value: str = Field(alias='theValue')


@pytest.mark.asyncio
@pytest.mark.parametrize('message', [
    JSONRPCResponse(jsonrpc='2.0', id=7, result={'n': 10 ** 100}),
    JSONRPCResponse(jsonrpc='2.0', id='7', result={'text': '한글😀\r\n\t\\"'}),
    JSONRPCResponse(jsonrpc='2.0', id='owned', result={'alias': AliasModel(value='한', extra='😀')}),
    JSONRPCResponse(jsonrpc='2.0', id='owned', result={'a': None, 'f': float('nan'), 'p': float('inf')}),
    JSONRPCError(jsonrpc='2.0', id=None, error=ErrorData(code=-32603, message='한글', data={'a': None})),
    JSONRPCNotification(jsonrpc='2.0', method='notifications/message', params={'data': '한글'}),
    JSONRPCRequest(jsonrpc='2.0', id='owned', method='tools/list'),
])
async def test_binary_wire_matches_sdk_alias_unset_json_and_exact_lf(message):
    stdin = IdleInput()
    with io.BytesIO() as output:
        async with retained_stdio_server(stdin=stdin, binary_stdout=output) as (read, write):
            async with read, write:
                await write.send(SessionMessage(message))
                await write.aclose()
                stdin.stop.set()
        assert output.getvalue() == message.model_dump_json(by_alias=True, exclude_unset=True).encode('utf-8') + b'\n'


@pytest.mark.asyncio
async def test_binary_topology_delivers_invalid_json_exception_then_valid_context_and_eof():
    marker = ContextVar('owned_context', default='missing')
    class Lines:
        def __aiter__(self):
            return self.lines()
        async def lines(self):
            marker.set('owned')
            yield '{invalid}\n'
            yield '{"jsonrpc":"2.0","id":7,"method":"tools/list"}\n'
    with io.BytesIO() as output:
        async with retained_stdio_server(stdin=Lines(), binary_stdout=output) as (read, write):
            async with read, write:
                received = [item async for item in read]
                assert isinstance(received[0], Exception)
                assert received[1].message.id == 7
                assert read.last_context.get(marker) == 'owned'
                await write.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['write', 'flush', 'serialize'])
async def test_binary_failure_releases_owner_and_tears_down(phase):
    class Broken(io.BytesIO):
        def write(self, data):
            if phase == 'write':
                raise BrokenPipeError('owned disconnected reader')
            return super().write(data)
        def flush(self):
            if phase == 'flush':
                raise BrokenPipeError('owned disconnected reader')
    stdin, owner = IdleInput(), ResponseOwner()
    text = '\ud800' if phase == 'serialize' else 'valid'
    message = JSONRPCResponse(jsonrpc='2.0', id=7, result={'text': text})
    with Broken() as output:
        with pytest.raises(BaseExceptionGroup):
            async with retained_stdio_server(stdin=stdin, binary_stdout=output) as (read, write):
                async with read, write:
                    stdio_delivery.get().register(7, owner)
                    await write.send(SessionMessage(message))
                    await asyncio.Event().wait()
        assert owner.released


@pytest.mark.asyncio
async def test_binary_partial_write_is_completed_before_flush():
    class Partial(io.BytesIO):
        def write(self, data):
            return super().write(data[:3])
    stdin = IdleInput()
    message = JSONRPCResponse(jsonrpc='2.0', id='owned', result={'value': '한글😀'})
    with Partial() as output:
        async with retained_stdio_server(stdin=stdin, binary_stdout=output) as (read, write):
            async with read, write:
                await write.send(SessionMessage(message))
                await write.aclose()
                stdin.stop.set()
        assert output.getvalue() == message.model_dump_json(by_alias=True, exclude_unset=True).encode() + b'\n'


@pytest.mark.asyncio
async def test_binary_disconnect_waits_for_worker_before_reservation_release():
    stdin, owner = IdleInput(), ResponseOwner()
    output = BinaryOutput(asyncio.get_running_loop())
    async def serve():
        async with retained_stdio_server(stdin=stdin, binary_stdout=output) as (read, write):
            async with read, write:
                stdio_delivery.get().register(7, owner)
                await write.send(SessionMessage(JSONRPCResponse(jsonrpc='2.0', id=7, result={'value': 'owned'})))
                await asyncio.Event().wait()
    server = asyncio.create_task(serve())
    try:
        await asyncio.wait_for(output.entered.wait(), 3)
        server.cancel()
        await asyncio.sleep(0)
        server.cancel()
        await asyncio.sleep(0)
        assert not owner.released
        assert not server.done()
        output.resume.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(server, 3)
        assert owner.released
    finally:
        output.resume.set()
        if not server.done():
            server.cancel()
            await asyncio.gather(server, return_exceptions=True)
        output.close()


@pytest.mark.asyncio
async def test_binary_duplicate_error_cannot_release_original_owner():
    class Observed(io.BytesIO):
        def __init__(self, loop):
            super().__init__()
            self.loop, self.flushed = loop, asyncio.Event()
        def flush(self):
            self.loop.call_soon_threadsafe(self.flushed.set)
    stdin, owner = IdleInput(), ResponseOwner()
    with Observed(asyncio.get_running_loop()) as output:
        async with retained_stdio_server(stdin=stdin, binary_stdout=output) as (read, write):
            async with read, write:
                registry = stdio_delivery.get()
                registry.register(7, owner)
                with pytest.raises(ValueError, match='already active'):
                    registry.register(7, ResponseOwner())
                await write.send(SessionMessage(JSONRPCError(jsonrpc='2.0', id=7,
                    error=ErrorData(code=-32603, message='duplicate'))))
                await asyncio.wait_for(output.flushed.wait(), 3)
                assert not owner.released
                await write.aclose()
                stdin.stop.set()
        assert owner.released


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [False, True])
async def test_default_binary_claim_is_restored_after_worker_settles(monkeypatch, failure):
    import transport.stdio_response_delivery as module
    class Destination(io.BytesIO):
        def write(self, data):
            if failure:
                raise BrokenPipeError('owned reader disconnected')
            return super().write(data)
    restored = []
    stdin, owner = IdleInput(), ResponseOwner()
    with Destination() as output:
        monkeypatch.setattr(module, '_claim_sdk_stdout', lambda: (output, lambda: restored.append(True)))
        async def run():
            async with retained_stdio_server(stdin=stdin) as (read, write):
                async with read, write:
                    stdio_delivery.get().register(7, owner)
                    await write.send(SessionMessage(JSONRPCResponse(jsonrpc='2.0', id=7, result={'ok': True})))
                    await write.aclose()
                    stdin.stop.set()
        if failure:
            with pytest.raises(BaseExceptionGroup):
                await run()
        else:
            await run()
        assert restored == [True]
        assert owner.released


@pytest.mark.asyncio
async def test_real_subprocess_cancel_restores_protected_stdout(tmp_path):
    # The child owns its descriptors; no parent/global stdout mutation occurs.
    src = Path(__file__).resolve().parents[1] / 'src'
    program = (
        'import asyncio,sys\n'
        f'sys.path.insert(0,{str(src)!r})\n'
        'from mcp.shared.message import SessionMessage\n'
        'from mcp.types import JSONRPCResponse\n'
        'from transport.stdio_response_delivery import retained_stdio_server\n'
        'class Empty:\n'
        ' def __aiter__(self): return self\n'
        ' async def __anext__(self): raise StopAsyncIteration\n'
        'async def run():\n'
        ' try:\n'
        '  async with retained_stdio_server(stdin=Empty()) as (read,write):\n'
        '   async with read,write:\n'
        "    print('owned diverted print',flush=True)\n"
        "    await write.send(SessionMessage(JSONRPCResponse(jsonrpc='2.0',id='owned',result={'ok':True})))\n"
        '    raise asyncio.CancelledError\n'
        ' except asyncio.CancelledError: pass\n'
        " print('owned restored stdout',flush=True)\n"
        'asyncio.run(run())\n'
    )
    process = await asyncio.create_subprocess_exec(sys.executable, '-c', program, cwd=tmp_path,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        output, error = await asyncio.wait_for(process.communicate(), 10)
        assert process.returncode == 0
        lines = output.splitlines()
        assert json.loads(lines[0]) == {'jsonrpc': '2.0', 'id': 'owned', 'result': {'ok': True}}
        assert lines[1:] == [b'owned restored stdout']
        assert b'owned diverted print' in error
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


@pytest.mark.asyncio
async def test_unsupported_context_stream_shape_fails_without_unretained_fallback(monkeypatch):
    import transport.stdio_response_delivery as module
    monkeypatch.setattr(module, 'sdk_stdio', SimpleNamespace(create_context_streams=lambda changed: ()))
    with io.BytesIO() as output:
        with pytest.raises(RuntimeError, match='context stream API is unsupported'):
            async with retained_stdio_server(stdin=IdleInput(), binary_stdout=output):
                pytest.fail('unsupported SDK shape was admitted')


@pytest.mark.asyncio
async def test_binary_writer_retains_until_flush_and_uses_one_worker():
    # Given an explicit binary destination whose flush is delayed.
    stdin = IdleInput()
    output = BinaryOutput(asyncio.get_running_loop())
    message = JSONRPCResponse(jsonrpc='2.0', id=7, result={'data': '한글😀\n' * 1000})
    owner = ResponseOwner()
    try:
        async with retained_stdio_server(stdin=stdin, binary_stdout=output) as (read, write):
            async with read, write:
                stdio_delivery.get().register(7, owner)
                await write.send(SessionMessage(message))
                await asyncio.wait_for(output.entered.wait(), 3)
                # Then handoff alone cannot release the reservation.
                assert not owner.released
                assert output.getvalue() == message.model_dump_json(by_alias=True, exclude_unset=True).encode() + b'\n'
                assert len(set(output.threads)) == 1
                assert output.threads[0] != threading.get_ident()
                output.resume.set()
                await write.aclose()
                stdin.stop.set()
        assert owner.released
    finally:
        output.resume.set()
        output.close()
