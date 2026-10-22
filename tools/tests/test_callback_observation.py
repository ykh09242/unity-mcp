"""Actual callback delays and released observers in the shared-read probe."""

import asyncio
import time

import pytest

from tools.experiments.service_optimizations import shared_read as probe


@pytest.mark.asyncio
async def test_callback_positive_control_observes_blocking_work_and_releases_handle():
    observer = probe.LoopCallbackObservation()
    await observer.start()
    until = time.perf_counter() + 0.02
    while time.perf_counter() < until:
        pass
    result = await observer.finish()
    assert result["callback_count"] >= 2 and result["maximum_gap_ms"] >= 20
    assert result["observer_released"] and observer.handle is None
    count = observer.count
    await asyncio.sleep(0.005)
    assert observer.count == count


@pytest.mark.asyncio
async def test_callback_cancellation_releases_pending_handle():
    observer = probe.LoopCallbackObservation()
    await observer.start()
    task = asyncio.create_task(observer.finish())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert observer.closed and observer.handle is None


@pytest.mark.asyncio
async def test_actual_shared_read_observation_preserves_complete_independent_outputs():
    value = {"data": {"rows": [{"id": i, "name": "original"} for i in range(100)]}}
    observation = {}
    ms, peak = await probe.measure("B", value, 4, callback_metrics=observation)
    assert ms > 0 and peak is None
    assert observation["observer_released"] and observation["callback_count"] >= 2
    assert value["data"]["rows"][0]["name"] == "original"


@pytest.mark.asyncio
async def test_measure_exception_releases_observer(monkeypatch):
    observers = []
    original = probe.LoopCallbackObservation

    def owned():
        result = original()
        observers.append(result)
        return result

    def fail(*_args, **_kwargs):
        raise RuntimeError("controlled task construction failure")

    monkeypatch.setattr(probe, "LoopCallbackObservation", owned)
    monkeypatch.setattr(probe.SharedToolReads, "session", fail)
    with pytest.raises(RuntimeError, match="controlled"):
        await probe.measure("B", {"data": {"rows": [{"name": "original"}]}}, 1, callback_metrics={})
    assert len(observers) == 1 and observers[0].closed and observers[0].handle is None
