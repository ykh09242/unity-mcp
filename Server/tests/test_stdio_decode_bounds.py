"""Admission and deadline behavior at the real stdio response decoder boundary."""

import asyncio
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from models.models import MCPResponse
from services.tools.shared_read_budget import SharedReadBudget
from tests import test_stdio_json_codec
from tests.timing import watchdog_timeout
import transport.legacy.unity_connection as uc

reply_connection = test_stdio_json_codec.reply_connection


@pytest.mark.parametrize("kind", ["depth", "nodes"])
def test_structural_overflow_is_refused_before_graph_allocation(
    reply_connection, monkeypatch, kind
):
    wire = (
        b'{"status":"success","result":'
        + (b"[[[[[0]]]]]" if kind == "depth" else b"[0,0,0,0,0,0,0,0,0,0]")
        + b"}"
    )
    monkeypatch.setattr(uc, "MAX_RESPONSE_DEPTH", 4, raising=False)
    monkeypatch.setattr(uc, "MAX_RESPONSE_NODES", 10, raising=False)
    calls = []
    original = uc.from_json

    def decode(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(uc, "from_json", decode)
    conn, sock = reply_connection(wire)
    result = conn.send_command("manage_gameobject", {"action": "modify"}, max_attempts=2)
    assert isinstance(result, MCPResponse) and result.data["reason"] == "outcome_unknown"
    assert calls == []
    assert len(sock.sent) == 2 and sock.closed


def test_decode_completion_after_deadline_is_unknown_without_replay(reply_connection, monkeypatch):
    conn, sock = reply_connection(b'{"status":"success","result":{"done":true}}')
    now = [10.0]
    original = uc.from_json

    def decode(*args, **kwargs):
        result = original(*args, **kwargs)
        now[0] = 21.0
        return result

    monkeypatch.setattr(uc.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(uc, "from_json", decode)
    result = conn.send_command("manage_gameobject", {"action": "modify"}, deadline=20.0)
    assert isinstance(result, MCPResponse) and result.data["reason"] == "outcome_unknown"
    assert len(sock.sent) == 2 and sock.closed


@pytest.mark.asyncio
async def test_cancelled_decode_keeps_graph_admission_until_actual_completion(
    reply_connection, monkeypatch
):
    graph_budget = SharedReadBudget(max_entries=1)
    monkeypatch.setattr(uc, "_stdio_decode_graph_budget", graph_budget, raising=False)
    entered, release = threading.Event(), threading.Event()
    original = uc.from_json
    calls = []

    def decode(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            entered.set()
            assert release.wait(watchdog_timeout())
        return original(*args, **kwargs)

    monkeypatch.setattr(uc, "from_json", decode)
    first, _ = reply_connection(b'{"status":"success","result":{"done":true}}')
    second, second_sock = reply_connection(b'{"status":"success","result":{"next":true}}')
    running = asyncio.create_task(
        uc._async_send_command_attempt(
            "manage_gameobject", {"action": "modify"}, _connection=first, max_retries=0
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, watchdog_timeout())
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        charged = graph_budget.retained_bytes
        assert charged > 0
        result = await uc._async_send_command_attempt(
            "manage_gameobject", {"action": "modify"}, _connection=second, max_retries=0
        )
        assert isinstance(result, MCPResponse) and result.data["reason"] == "outcome_unknown"
        assert len(calls) == 1 and len(second_sock.sent) == 2
        assert graph_budget.retained_bytes == charged
    finally:
        release.set()
        await asyncio.gather(running, return_exceptions=True)
        # The response worker retains the connection lock until its accounting settles.
        await asyncio.to_thread(first.disconnect)
    assert graph_budget.retained_bytes == 0


def test_expired_preparse_does_not_start_graph_decode(reply_connection, monkeypatch):
    conn, sock = reply_connection(b'{"status":"success","result":{"done":true}}')
    now = [10.0]
    original = uc.bounded_json_profile
    calls = []

    def profile(*args, **kwargs):
        result = original(*args, **kwargs)
        now[0] = 21.0
        return result

    monkeypatch.setattr(uc.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(uc, "bounded_json_profile", profile)
    monkeypatch.setattr(uc, "from_json", lambda *args, **kwargs: calls.append(True) or {})
    result = conn.send_command("manage_gameobject", {"action": "modify"}, deadline=20.0)
    assert isinstance(result, MCPResponse) and result.data["reason"] == "outcome_unknown"
    assert calls == [] and len(sock.sent) == 2


def test_ascii_unicode_escape_reserves_decoded_wide_string(monkeypatch):
    budget = SharedReadBudget(max_bytes=100_000)
    monkeypatch.setattr(uc, "_stdio_decode_graph_budget", budget)
    calls = []
    monkeypatch.setattr(uc, "from_json", lambda *args, **kwargs: calls.append(True) or {})
    wire = b'{"text":"' + b"a" * 30_000 + b'\\ud83d\\ude00"}'
    with pytest.raises(uc._UnityProtocolError, match="capacity"):
        uc._decode_unity_response(wire)
    assert calls == [] and budget.retained_bytes == 0


def test_small_responses_can_decode_concurrently_and_release_on_error(monkeypatch):
    barrier = threading.Barrier(5, timeout=watchdog_timeout())
    graph = SharedReadBudget()
    monkeypatch.setattr(uc, "_stdio_decode_graph_budget", graph)
    original = uc.from_json

    def decode(*args, **kwargs):
        barrier.wait()
        return original(*args, **kwargs)

    monkeypatch.setattr(uc, "from_json", decode)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(uc._decode_unity_response, b'{"value":7}') for _ in range(4)]
        barrier.wait()
        assert [future.result(watchdog_timeout()) for future in futures] == [{"value": 7}] * 4
    assert graph.retained_bytes == 0
    monkeypatch.setattr(uc, "from_json", original)
    with pytest.raises(ValueError):
        uc._decode_unity_response(b'{"value":invalid}')
    assert graph.retained_bytes == 0


def test_raw_admission_counts_bytearray_spare_capacity_with_wide_unicode(monkeypatch):
    wire = b'{"text":"' + b"a" * (1024 * 1024) + "😀".encode() + b'"}'
    raw = bytearray()
    for offset in range(0, len(wire), 65_536):
        raw.extend(wire[offset : offset + 65_536])
    logical_charge = 5 * len(raw) + 4096
    actual_storage = sys.getsizeof(raw) + sys.getsizeof(raw.decode("utf-8"))
    assert actual_storage > logical_charge  # This fixture has real spare allocation.
    budget = SharedReadBudget(max_bytes=(logical_charge + actual_storage) // 2)
    monkeypatch.setattr(uc, "_stdio_decode_raw_budget", budget)
    calls = []
    original = uc.from_json

    def decode(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(uc, "from_json", decode)
    with pytest.raises(uc._UnityProtocolError, match="capacity"):
        uc._decode_unity_response(raw)
    assert calls == [] and budget.retained_bytes == 0
