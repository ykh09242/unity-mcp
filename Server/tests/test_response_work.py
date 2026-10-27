"""Response CPU workers retain ownership until actual completion."""

from tests.timing import await_signal, watchdog_timeout

import asyncio
import threading

import anyio
import pytest

from models.response_limits import ResponseOwner, response_owner
from services.tools.shared_tool_reads import SharedToolReads


@pytest.mark.asyncio
@pytest.mark.parametrize("admitted", [False, True])
async def test_final_sizing_precedes_owned_worker_cloning(monkeypatch, admitted):
    import services.tools.shared_tool_reads as module

    # The primitive graph is large, but deterministic thread identity is the
    # regression oracle; throughput and GIL gaps are measured separately.
    value = {"rows": [{"label": "x" * 1024} for _ in range(300)]}
    loop_thread = threading.get_ident()
    original_size, original_copy = module.response_size, module.deepcopy
    seen = []

    def checked_size(*args, **kwargs):
        seen.append("size")
        assert threading.get_ident() == loop_thread
        return original_size(*args, **kwargs)

    def checked_copy(*args, **kwargs):
        seen.append("copy")
        assert threading.get_ident() != loop_thread
        return original_copy(*args, **kwargs)

    monkeypatch.setattr(module, "response_size", checked_size)
    monkeypatch.setattr(module, "deepcopy", checked_copy)

    async def factory():
        if admitted:
            assert module.shared_read_budget.reserve(response_owner.get(), original_size(value))
        return value

    async with SharedToolReads().session("worker") as shared:
        result = await shared.fetch(factory)
    assert result == value and result is not value
    assert seen == ["size", "copy"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["asyncio", "anyio"])
async def test_cancelled_clone_holds_its_bytes_until_worker_exit(monkeypatch, mode):
    import services.tools.shared_tool_reads as module

    value = {"rows": [{"label": "x" * 1024} for _ in range(300)]}
    entered, release = threading.Event(), threading.Event()
    original = module.deepcopy
    owner = ResponseOwner()
    baseline = module.shared_read_budget.retained_bytes

    def copy_work(*args):
        entered.set()
        assert release.wait(watchdog_timeout()), "test must release its owned worker"
        return original(*args)

    monkeypatch.setattr(module, "deepcopy", copy_work)
    scopes = []

    async def factory():
        return value

    async def consume():
        with anyio.CancelScope() as scope:
            scopes.append(scope)
            token = response_owner.set(owner)
            try:
                async with SharedToolReads().session("cancel-worker") as shared:
                    await shared.fetch(factory)
            finally:
                response_owner.reset(token)
                owner.release()

    task = asyncio.create_task(consume())
    try:
        assert await asyncio.to_thread(entered.wait, watchdog_timeout())
        charged = module.shared_read_budget.retained_bytes
        assert charged > baseline
        if mode == "asyncio":
            task.cancel()
        else:
            scopes[0].cancel()
        for _ in range(10):
            await asyncio.sleep(0)
        assert not task.done()
        assert module.shared_read_budget.retained_bytes == charged
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert owner.entries == []
    assert module.shared_read_budget.retained_bytes == baseline


@pytest.mark.asyncio
async def test_workers_are_bounded_and_queued_cancellation_does_not_submit():
    from services.tools.response_work import response_work

    gate = threading.Event()
    guard = threading.Lock()
    started = []

    def operation(number):
        with guard:
            started.append(number)
        assert gate.wait(watchdog_timeout())
        return number

    tasks = [
        asyncio.create_task(response_work(lambda n=n: operation(n), large=True)) for n in range(6)
    ]
    try:
        async with asyncio.timeout(watchdog_timeout()):
            while len(started) < 2:
                await asyncio.sleep(0.001)
        for _ in range(10):
            await asyncio.sleep(0)
        assert started == [0, 1]
        tasks[-1].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[-1]
        assert started == [0, 1]
        gate.set()
        assert await asyncio.gather(*tasks[:-1]) == list(range(5))
    finally:
        gate.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert sorted(started) == list(range(5))


@pytest.mark.asyncio
async def test_copy_plan_fallback_does_not_call_metaclass_equality():
    from tests.test_response_limits_encoding import CustomList
    from models.response_limits import ResponseCopyPlan, response_size

    value = CustomList([1, 2])
    plan = ResponseCopyPlan()
    assert response_size(value, copy_plan=plan) is not None
    assert not plan.shareable and plan.detached_bytes is None


@pytest.mark.asyncio
async def test_cancellation_aborts_an_unstarted_executor_operation(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import gc
    import weakref
    from services.tools import response_work as module

    executor = ThreadPoolExecutor(max_workers=1)
    gate, entered = threading.Event(), threading.Event()
    loop = asyncio.get_running_loop()

    def occupy():
        entered.set()
        assert gate.wait(watchdog_timeout())

    blocker = loop.run_in_executor(executor, occupy)
    assert await asyncio.to_thread(entered.wait, watchdog_timeout())
    original = module.submit_owned_executor
    submitted = asyncio.Event()

    def submit(owner_loop, _executor, operation):
        result = original(owner_loop, executor, operation)
        submitted.set()
        return result

    monkeypatch.setattr(module, "submit_owned_executor", submit)

    class Payload:
        pass

    payload = Payload()
    retained = weakref.ref(payload)
    invoked = []
    task = asyncio.create_task(
        module.response_work(lambda item=payload: invoked.append(item), large=True)
    )
    del payload
    try:
        await await_signal(submitted, "response work submitted", producers=(task,))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(task), watchdog_timeout())
        gc.collect()
        assert retained() is None
        assert not invoked
    finally:
        gate.set()
        await blocker
        await asyncio.gather(task, return_exceptions=True)
        executor.shutdown(wait=True)
