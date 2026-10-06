"""Native Hub JSON decoding preserves the existing bounds and protocol contracts."""
import asyncio
import gzip
import json
import math
import struct
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio

from transport import plugin_hub as hub_module
from transport.large_result_assembler import CAPABILITY, COMPRESSION_CAPABILITY, CHUNK_PAYLOAD_BYTES, MAGIC
from transport.plugin_hub import PluginHub
from transport.charge_ledger import ChargeLedger
from transport.plugin_registry import PluginRegistry

COMMAND_ID = '11111111-2222-4333-8444-555555555555'


@pytest_asyncio.fixture
async def owned_hub():
    class OwnedHub(PluginHub):
        _registry = None
        _connections = {}
        _pending = {}
        _retained_results = ChargeLedger()
        _ping_tasks = {}
        _last_pong = {}
        _admitted = {}
    OwnedHub.configure(PluginRegistry())
    websocket = SimpleNamespace(state=SimpleNamespace(plugin_generation='owned', plugin_features={CAPABILITY}),
                                close=AsyncMock())
    OwnedHub._connections['owned'] = websocket
    previous_limit = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(sys.int_info.default_max_str_digits)
    try:
        yield OwnedHub.__new__(OwnedHub), websocket
    finally:
        OwnedHub._large_results.discard_owner('owned')
        sys.set_int_max_str_digits(previous_limit)


def exact(value):
    if isinstance(value, float):
        return ('float', value.hex(), math.copysign(1, value))
    if isinstance(value, dict):
        return {key: exact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [exact(item) for item in value]
    return (type(value).__name__, value)


@pytest.mark.asyncio
@pytest.mark.parametrize('binary', [False, True])
async def test_hub_valid_decode_avoids_stdlib_parser(owned_hub, monkeypatch, binary):
    hub, websocket = owned_hub
    text = '{"type":"command_result","id":"owned","result":{"text":"한글 😀","value":123}}'
    expected = json.loads(text)
    parser = Mock(wraps=json.loads)
    monkeypatch.setattr(hub_module.json, 'loads', parser)
    raw = {'bytes': text.encode()} if binary else {'text': text}
    assert await hub.decode(websocket, raw) == expected
    assert parser.call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('text', [
    '{"a":NaN,"b":Infinity,"c":-Infinity,"d":1e400}',
    '{"a":-0.0,"b":5e-324,"c":2.2250738585072012e-308}',
    '{"a":18446744073709551616,"b":-18446744073709551616}',
    '{"a":' + '9' * 200 + '}', '{"a":"\\ud800"}',
    '{"a":"\\ud83d\\ude00","b":"한글 😀"}', '{"a":1,"a":2}', '[1,2]', 'null',
])
async def test_hub_preserves_stdlib_values(owned_hub, text):
    hub, websocket = owned_hub
    assert exact(await hub.decode(websocket, {'bytes': text.encode()})) == exact(json.loads(text))


@pytest.mark.asyncio
@pytest.mark.parametrize('limit', [640, sys.int_info.default_max_str_digits, 0])
@pytest.mark.parametrize('digits', [1000, 5000])
async def test_hub_preserves_configured_integer_digit_limit(owned_hub, limit, digits):
    hub, websocket = owned_hub
    sys.set_int_max_str_digits(limit)
    text = '{"nested":[{"integer":' + '9' * digits + '}]}'
    try:
        expected = json.loads(text)
    except ValueError:
        assert await hub.decode(websocket, {'text': text}) is None
        websocket.close.assert_awaited_once_with(code=1003, reason='Malformed plugin message')
    else:
        assert await hub.decode(websocket, {'text': text}) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('raw,code', [(b'{"a":1}trailing', 1003), (b'\xef\xbb\xbf{}', 1003),
                                    (b'{"a":"\xff"}', 1009), (b'{"a":"\x00"}', 1003), (b'{"a":', 1003)])
async def test_hub_malformed_utf8_and_json_keep_public_close_codes(owned_hub, raw, code):
    hub, websocket = owned_hub
    assert await hub.decode(websocket, {'bytes': raw}) is None
    assert websocket.close.await_args.kwargs['code'] == code


@pytest.mark.asyncio
async def test_hub_custom_byte_decode_still_controls_bounded_payload(owned_hub):
    hub, websocket = owned_hub
    class CustomBytes(bytes):
        def decode(self, encoding='utf-8', errors='strict'):
            return '{"owned":"custom-hook"}'
    assert await hub.decode(websocket, {'bytes': CustomBytes(b'{"owned":"original"}')}) == {'owned': 'custom-hook'}


@pytest.mark.asyncio
async def test_completed_large_result_avoids_stdlib_and_releases_raw_buffer(owned_hub, monkeypatch):
    hub, websocket = owned_hub
    future = asyncio.get_running_loop().create_future()
    type(hub)._pending[COMMAND_ID] = {'future': future, 'session_id': 'owned', 'user_id': None}
    expected = {'success': True, 'text': 'x' * (256 * 1024)}
    wire = json.dumps({'type': 'command_result', 'id': COMMAND_ID, 'result': expected}).encode()
    await hub._handle_large_result(websocket, {'type': 'result_start', 'id': COMMAND_ID,
        'total_bytes': len(wire), 'chunk_count': math.ceil(len(wire) / CHUNK_PAYLOAD_BYTES)})
    parser = Mock(wraps=json.loads)
    monkeypatch.setattr(hub_module.json, 'loads', parser)
    for offset in range(0, len(wire), CHUNK_PAYLOAD_BYTES):
        frame = MAGIC + COMMAND_ID.encode('ascii') + struct.pack('>I', offset) + wire[offset:offset + CHUNK_PAYLOAD_BYTES]
        await hub._handle_large_result(websocket, frame)
    assert future.result() == expected
    assert parser.call_count == 0
    assert type(hub)._raw_results == {} and type(hub)._large_results.retained_bytes == 0
    websocket.close.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['bytes', 'depth', 'nodes'])
async def test_preparse_limits_reject_before_native_decoder(owned_hub, monkeypatch, kind):
    hub, websocket = owned_hub
    parser = Mock(side_effect=AssertionError('graph allocation must follow preparse admission'))
    monkeypatch.setattr(hub_module, 'decode_json', parser)
    raw = b'{"a":[1,2]}'
    limit = {'bytes': 'MAX_RAW_MESSAGE_BYTES', 'depth': 'MAX_RESULT_DEPTH', 'nodes': 'MAX_RESULT_NODES'}[kind]
    monkeypatch.setattr(type(hub), limit, 1)
    assert await hub.decode(websocket, {'bytes': raw}) is None
    parser.assert_not_called()
    assert websocket.close.await_args.kwargs['code'] == 1009


@pytest.mark.asyncio
@pytest.mark.parametrize('invalidation', ['cancel', 'disconnect', 'replacement'])
async def test_late_final_frame_does_not_decode_or_retain(owned_hub, monkeypatch, invalidation):
    hub, websocket = owned_hub
    future = asyncio.get_running_loop().create_future()
    type(hub)._pending[COMMAND_ID] = {'future': future, 'session_id': 'owned', 'user_id': None}
    wire = json.dumps({'type': 'command_result', 'id': COMMAND_ID,
                       'result': {'text': 'x' * (256 * 1024)}}).encode()
    await hub._handle_large_result(websocket, {'type': 'result_start', 'id': COMMAND_ID,
        'total_bytes': len(wire), 'chunk_count': math.ceil(len(wire) / CHUNK_PAYLOAD_BYTES)})
    frames = [MAGIC + COMMAND_ID.encode('ascii') + struct.pack('>I', offset) + wire[offset:offset + CHUNK_PAYLOAD_BYTES]
              for offset in range(0, len(wire), CHUNK_PAYLOAD_BYTES)]
    for frame in frames[:-1]:
        await hub._handle_large_result(websocket, frame)
    if invalidation == 'cancel':
        future.cancel()
    elif invalidation == 'disconnect':
        type(hub)._connections.clear()
    else:
        type(hub)._connections['owned'] = SimpleNamespace(state=SimpleNamespace(plugin_generation='replacement'))
    parser = Mock(side_effect=AssertionError('late result must not allocate a graph'))
    monkeypatch.setattr(hub_module, 'decode_json', parser)
    await hub._handle_large_result(websocket, frames[-1])
    parser.assert_not_called()
    assert type(hub)._raw_results == {} and type(hub)._large_results.retained_bytes == 0
    future.cancel()


@pytest.mark.asyncio
@pytest.mark.parametrize('surrogate', [False, True])
async def test_gzip_final_frame_uses_compatible_decode_and_releases_all_reservations(owned_hub, surrogate):
    hub, websocket = owned_hub
    websocket.state.plugin_features.add(COMPRESSION_CAPABILITY)
    future = asyncio.get_running_loop().create_future()
    type(hub)._pending[COMMAND_ID] = {'future': future, 'session_id': 'owned', 'user_id': None}
    expected = {'success': True, 'text': 'x' * (1024 * 1024)}
    if surrogate:
        expected['surrogate'] = '\ud800'
    decoded = json.dumps({'type': 'command_result', 'id': COMMAND_ID, 'result': expected}).encode()
    wire = gzip.compress(decoded)
    await hub._handle_large_result(websocket, {'type': 'result_start', 'id': COMMAND_ID,
        'total_bytes': len(wire), 'chunk_count': math.ceil(len(wire) / CHUNK_PAYLOAD_BYTES),
        'encoding': 'gzip', 'decoded_bytes': len(decoded)})
    for offset in range(0, len(wire), CHUNK_PAYLOAD_BYTES):
        frame = MAGIC + COMMAND_ID.encode('ascii') + struct.pack('>I', offset) + wire[offset:offset + CHUNK_PAYLOAD_BYTES]
        await hub._handle_large_result(websocket, frame)
    if surrogate:
        # Decoding accepts legacy escaped surrogates, but the unchanged final
        # response serializer bound rejects their invalid UTF-8 output.
        assert future.result()['data']['reason'] == 'result_payload_limit'
    else:
        assert future.result() == expected
    assert type(hub)._raw_results == {} and type(hub)._large_results.retained_bytes == 0
    websocket.close.assert_not_awaited()
