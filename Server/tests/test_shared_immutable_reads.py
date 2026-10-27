"""Immutable fan-out preserves mutable responses, ownership and fallback behavior."""

from tests.timing import await_signal
import asyncio
from copy import deepcopy
import math

import pytest

from models.response_limits import ResponseOwner
from services.tools.shared_read_budget import shared_read_budget
from services.tools.shared_tool_reads import SharedToolReads


@pytest.mark.asyncio
@pytest.mark.parametrize("readers,prepared", [(1, 0), (3, 0), (4, 0), (16, 1)])
async def test_only_concurrent_fanout_prepares_and_every_response_is_detached(readers, prepared):
    from services.tools.immutable_json import prepare_json_copy

    calls, preparations = 0, 0
    original = {"rows": [{"id": 2**256, "name": "한글😀", "values": [-0.0, None]}]}

    def prepare(value):
        nonlocal preparations
        preparations += 1
        return prepare_json_copy(value)

    async def fetch():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return original

    before = shared_read_budget.retained_bytes
    reads = SharedToolReads(freshness_s=1, copy_preparer=prepare)
    async with reads.session("same") as read:
        results = await asyncio.gather(*(read.fetch(fetch) for _ in range(readers)))
        assert results == [original] * readers
        assert calls == 1 and preparations == prepared
        assert math.copysign(1, results[0]["rows"][0]["values"][0]) == -1
        results[0]["rows"][0]["name"] = "changed"
        assert original["rows"][0]["name"] == "한글😀"
        if readers > 1:
            assert results[1]["rows"][0]["name"] == "한글😀"
    assert shared_read_budget.retained_bytes == before


@pytest.mark.asyncio
async def test_preparation_capacity_fallback_does_not_reject_existing_work(monkeypatch):
    calls = 0
    original_reserve = ResponseOwner.reserve_copy

    def reserve(self):
        nonlocal calls
        calls += 1
        return None if calls == 1 else original_reserve(self)

    def prepare(value):
        pytest.fail("Preparation must be skipped without temporary admission")

    async def fetch():
        await asyncio.sleep(0)
        return {"rows": [1, 2, 3]}

    monkeypatch.setattr(ResponseOwner, "reserve_copy", reserve)
    before = shared_read_budget.retained_bytes
    reads = SharedToolReads(freshness_s=1, copy_preparer=prepare)
    async with reads.session("same") as read:
        result = await asyncio.gather(*(read.fetch(fetch) for _ in range(16)))
    assert result == [{"rows": [1, 2, 3]}] * 16
    assert calls == 17
    assert shared_read_budget.retained_bytes == before


def test_frozen_snapshot_owns_containers_and_preserves_scalar_fidelity():
    from services.tools.immutable_json import prepare_json_copy

    source = {"rows": [2**256, -(2**127), -0.0, "한글😀", float("inf"), None, True]}
    expected = deepcopy(source)
    clone = prepare_json_copy(source)
    assert clone is not None
    source["rows"].clear()
    first, second = clone(), clone()
    assert first == expected and second == expected
    first["rows"].clear()
    assert second == expected


def test_alias_cycle_and_custom_objects_use_compatible_deepcopy_path():
    from services.tools.immutable_json import prepare_json_copy

    shared = []
    alias = {"one": shared, "two": shared}
    cycle = []
    cycle.append(cycle)

    class CustomDict(dict):
        pass

    for value in (alias, cycle, CustomDict(a=1), {1: "key"}, {"tuple": (1, 2)}):
        assert prepare_json_copy(value) is None
    copied = deepcopy(alias)
    assert copied["one"] is copied["two"]


@pytest.mark.asyncio
async def test_cancelled_joiner_does_not_cancel_immutable_flight_or_leak_budget():
    from services.tools.immutable_json import prepare_json_copy

    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return {"rows": [{"id": 42}]}

    before = shared_read_budget.retained_bytes
    reads = SharedToolReads(freshness_s=1, copy_preparer=prepare_json_copy)
    async with reads.session("same") as read:
        tasks = [asyncio.create_task(read.fetch(fetch)) for _ in range(17)]
        try:
            await await_signal(started, "immutable fetch started", producers=tasks)
            tasks[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await tasks[0]
            release.set()
            results = await asyncio.gather(*tasks[1:])
            assert calls == 1 and results == [{"rows": [{"id": 42}]}] * 16
            results[0]["rows"].clear()
            assert results[1]["rows"] == [{"id": 42}]
        finally:
            release.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    assert shared_read_budget.retained_bytes == before
