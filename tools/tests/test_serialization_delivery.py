"""Compatibility and settled-write lifetime for the unadopted lower-bound probe."""

import asyncio
from copy import deepcopy
import gc
import json
import struct
import threading
import weakref

from pydantic import BaseModel
import pytest

from tools.experiments.service_optimizations.serialization_delivery import (
    DigestSink,
    deliver,
    prepare_frames,
    sdk_frame,
)
from transport.stdio_response_delivery import _settled_binary_write


@pytest.mark.parametrize("request_id", [1, "1", "\ud55c\uae00\U0001f600", 2**100])
def test_prepared_wire_matches_sdk_without_losing_scalar_values(request_id):
    value = {
        "rows": [
            {
                "integer": 2**200,
                "negative": -(2**200),
                "zero": -0.0,
                "subnormal": 5e-324,
                "unicode": "\ud55c\uae00\U0001f600",
                "flag": True,
            }
        ]
    }
    frozen = deepcopy(value)
    frames = prepare_frames(value)
    assert frames is not None
    assert frames(request_id) == sdk_frame(value, request_id)
    value["rows"][0]["integer"] = 0
    assert frames(request_id) == sdk_frame(frozen, request_id)
    parsed = json.loads(frames(request_id))["result"]["structuredContent"]["rows"][0]
    assert parsed["integer"] == 2**200
    for name in ("zero", "subnormal"):
        assert struct.pack("!d", parsed[name]) == struct.pack("!d", frozen["rows"][0][name])


class Row(BaseModel):
    number: int


@pytest.mark.parametrize("value", [{"row": Row(number=7)}, {"rows": (1, 2, 3)}])
@pytest.mark.asyncio
async def test_unsupported_shapes_keep_baseline_fallback(value):
    assert prepare_frames(value) is None
    assert await deliver(value, 2, True) == await deliver(value, 2, False)


def test_aliases_and_cycles_are_not_shared_as_prepared_frames():
    shared = []
    assert prepare_frames({"left": shared, "right": shared}) is None
    shared.append(shared)
    assert prepare_frames({"cycle": shared}) is None


@pytest.mark.asyncio
async def test_cancelled_write_remains_owned_until_real_flush_then_releases_sink():
    async def exercise():
        entered = threading.Event()
        release = threading.Event()

        class BlockedSink(DigestSink):
            def flush(self):
                entered.set()
                if not release.wait(10):
                    raise TimeoutError("Test did not release actual writer")
                super().flush()

        sink = BlockedSink()
        weak = weakref.ref(sink)
        frame = prepare_frames({"rows": list(range(100))})("owned")
        task = asyncio.create_task(_settled_binary_write(sink, frame))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            assert sink.flushes == 0
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert sink.flushes == 1
        assert sink.bytes == len(frame) + 1
        return weak

    reference = await exercise()
    # The finished task's callbacks and cancellation traceback settle separately.
    for _ in range(5):
        await asyncio.sleep(0)
    gc.collect()
    assert reference() is None
