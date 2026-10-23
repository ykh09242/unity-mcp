"""Large transfers spend the admitted command budget without renewing it."""

import asyncio
import gzip
import json
import random

import pytest

from tests.test_http_command_outcome import command_hub  # pylint: disable=unused-import
from tests.test_large_result_assembler import Budget, COMMAND_ID, OWNER, frames
from transport.command_budget import _CommandBudget
from transport.large_result_assembler import (
    CHUNK_PAYLOAD_BYTES,
    COMPRESSION_THRESHOLD_BYTES,
    MAGIC,
    MAX_RESULT_BYTES,
    THRESHOLD_BYTES,
    LargeResultAssembler,
    LargeResultProtocolError,
)
from transport.plugin_hub import PluginHub


@pytest.mark.parametrize("seconds", [900, 86400])
def test_standalone_explicit_long_budget_remains_finite(seconds):
    budget = Budget()
    assembler = LargeResultAssembler(
        lambda *_: True,
        budget.reserve,
        budget.release,
        timeout_seconds=seconds,
        clock=lambda: budget.now,
    )
    assembler.begin(OWNER, COMMAND_ID, THRESHOLD_BYTES, 5)
    budget.now += 301
    assert assembler.expire() == ()
    budget.now = 10 + seconds
    assert assembler.expire() == ((OWNER, COMMAND_ID),)
    assert not budget.reservations


@pytest.mark.parametrize(
    "seconds", [True, False, 0, -1, 86401, float("nan"), float("inf"), "900", 10**500]
)
def test_standalone_budget_rejects_invalid_values(seconds):
    with pytest.raises(LargeResultProtocolError, match="invalid_assembler_limits"):
        LargeResultAssembler(
            lambda *_: True, lambda *_: True, lambda *_: None, timeout_seconds=seconds
        )


@pytest.mark.parametrize("compressed", [False, True])
def test_progress_beyond_five_minutes_uses_inherited_budget(compressed):
    budget = Budget()
    assembler = budget.assembler()
    if compressed:
        raw = random.Random(23).randbytes(COMPRESSION_THRESHOLD_BYTES)
        wire = gzip.compress(raw, mtime=0)
        assembler._reserve_compressed = lambda owner, cid, size, _working: budget.reserve(
            owner, cid, size
        )
    else:
        raw = wire = b"x" * THRESHOLD_BYTES
    chunks = frames(wire)
    assembler.begin(
        OWNER,
        COMMAND_ID,
        len(wire),
        len(chunks),
        timeout_seconds=900,
        encoding="gzip" if compressed else "identity",
        decoded_bytes=len(raw) if compressed else None,
    )
    result = None
    for index, chunk in enumerate(chunks):
        budget.now = 10 + (301 * index / (len(chunks) - 1))
        result = assembler.feed(OWNER, chunk)
    assert result.payload == raw
    assembler.discard(OWNER, COMMAND_ID)
    assert assembler.retained_bytes == 0 and not budget.reservations


def test_maximum_size_result_can_finish_after_five_minutes():
    budget = Budget()
    assembler = budget.assembler()
    size = MAX_RESULT_BYTES
    assembler.begin(
        OWNER,
        COMMAND_ID,
        size,
        (size + CHUNK_PAYLOAD_BYTES - 1) // CHUNK_PAYLOAD_BYTES,
        timeout_seconds=900,
    )
    # Generate one frame at a time: the fixture must not retain a second 64 MiB payload.
    result = None
    for offset in range(0, size, CHUNK_PAYLOAD_BYTES):
        budget.now = 10 + 301 * offset / size
        frame = MAGIC + COMMAND_ID.encode() + offset.to_bytes(4, "big")
        frame += b"x" * min(CHUNK_PAYLOAD_BYTES, size - offset)
        result = assembler.feed(OWNER, frame)
    assert len(result.payload) == size and result.payload[0] == result.payload[-1] == ord("x")
    assembler.discard(OWNER, COMMAND_ID)
    assert assembler.retained_bytes == 0 and not budget.reservations


@pytest.mark.parametrize("progress", [False, True])
def test_inherited_deadline_expires_without_progress_extensions(progress):
    budget = Budget()
    assembler = budget.assembler()
    assembler.begin(OWNER, COMMAND_ID, THRESHOLD_BYTES, 5, timeout_seconds=15)
    if progress:
        budget.now += 14
        assembler.feed(OWNER, frames(b"x" * THRESHOLD_BYTES)[0])
    budget.now = 25
    assert assembler.expire() == ((OWNER, COMMAND_ID),)
    assert assembler.retained_bytes == 0 and not budget.reservations


@pytest.mark.parametrize(
    "seconds,reason",
    [
        (0, "result_transfer_timeout"),
        (-1, "result_transfer_timeout"),
        (True, "invalid_result_timeout"),
        (float("nan"), "invalid_result_timeout"),
        (float("inf"), "invalid_result_timeout"),
        ("900", "invalid_result_timeout"),
        (10**500, "invalid_result_timeout"),
    ],
)
def test_invalid_or_spent_command_budget_is_rejected_before_reservation(seconds, reason):
    budget = Budget()
    assembler = budget.assembler()
    with pytest.raises(LargeResultProtocolError, match=reason):
        assembler.begin(OWNER, COMMAND_ID, THRESHOLD_BYTES, 5, timeout_seconds=seconds)
    assert assembler.retained_bytes == 0 and not budget.reservations


def test_cancelled_long_transfer_releases_and_ignores_late_chunks():
    budget = Budget()
    assembler = budget.assembler()
    assembler.begin(OWNER, COMMAND_ID, THRESHOLD_BYTES, 5, timeout_seconds=900)
    budget.pending.clear()
    assert assembler.feed(OWNER, frames(b"x" * THRESHOLD_BYTES)[0]) is None
    assert assembler.retained_bytes == 0 and not budget.reservations


@pytest.mark.asyncio
@pytest.mark.parametrize("remaining", [400, 86405])
async def test_hub_admission_carries_remaining_budget_through_real_result_dispatch(
    command_hub, remaining
):
    command_hub.websocket.state.plugin_features = frozenset({"large_result_v1"})
    budget = _CommandBudget(asyncio.get_running_loop().time() + remaining)
    transfer_clock = [1000.0]  # Deliberately a different epoch from the event-loop clock.
    PluginHub._large_results._clock = lambda: transfer_clock[0]

    async def send_json(message):
        command_hub.sent.append(message)
        assert message["type"] == "execute"
        cid = message["id"]
        assert PluginHub._pending[cid].get("result_deadline") == budget.expires_at
        raw = json.dumps(
            {
                "type": "command_result",
                "id": cid,
                "result": {"success": True, "body": "x" * THRESHOLD_BYTES},
            }
        ).encode()
        chunks = frames(raw, cid)
        await command_hub.endpoint._handle_large_result(
            command_hub.websocket,
            {
                "type": "result_start",
                "id": cid,
                "total_bytes": len(raw),
                "chunk_count": len(chunks),
                "timeout_seconds": 10**9,  # Peer data never controls the admitted deadline.
            },
        )
        transfer = PluginHub._large_results._transfers[("generation", cid)]
        assert transfer.deadline == pytest.approx(1000 + budget.remaining(), abs=0.1)
        for index, chunk in enumerate(chunks):
            transfer_clock[0] = 1000 + 301 * index / (len(chunks) - 1)
            await command_hub.endpoint._handle_large_result(command_hub.websocket, chunk)

    command_hub.websocket.send_json = send_json
    result = await PluginHub.send_command(
        "session", "run_tests", {"timeout_seconds": 86400}, _budget=budget
    )
    assert result["success"] and result["body"] == "x" * THRESHOLD_BYTES
    assert len(command_hub.sent) == 1
    assert PluginHub._pending == {} and PluginHub._large_results.retained_bytes == 0
    assert not PluginHub._raw_results
