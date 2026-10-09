"""Public file searches share bounded worker ownership with script edits."""

import asyncio
import base64
import gc
import importlib
import json
import weakref
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from threading import Event
from types import SimpleNamespace

import pytest
from fastmcp import Client, FastMCP
from services.tools import bounded_regex

search = importlib.import_module("services.tools.find_in_file")
selection = importlib.import_module("services.tools.script_apply_edits")


class Source(str):
    """Observe script inputs without an observer owning the producer result."""


class CountingPool(ThreadPoolExecutor):
    def __init__(self, workers):
        super().__init__(max_workers=workers)
        self.submissions = 0

    def submit(self, *args, **kwargs):
        self.submissions += 1
        return super().submit(*args, **kwargs)


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.001)


@pytest.fixture
def routing(monkeypatch):
    state = SimpleNamespace(source="first\nneedle NEEDLE\nlast", refs=[], reads=0, encoded=False)

    async def target(_ctx):
        return "Synthetic@abc123"

    async def read(_send, instance, command, params):
        assert instance == "Synthetic@abc123" and command == "manage_script"
        assert params == {"action": "read", "name": "Test", "path": "Assets"}
        state.reads += 1
        source = Source(state.source)
        state.refs.append(weakref.ref(source))
        if state.encoded:
            return {
                "success": True,
                "data": {
                    "contentsEncoded": True,
                    "encodedContents": base64.b64encode(source.encode()).decode(),
                },
            }
        return {"success": True, "data": {"contents": source}}

    monkeypatch.setattr(search, "get_unity_instance_from_context", target)
    monkeypatch.setattr(search, "send_with_unity_instance", read)
    return state


@pytest.fixture
def executor(monkeypatch):
    pools = []
    releases = []

    def install(workers):
        loop = asyncio.get_running_loop()
        original = loop.run_in_executor
        pool = CountingPool(workers)
        pools.append(pool)

        def submit(selected, func, *args):
            return original(pool if selected is None else selected, func, *args)

        monkeypatch.setattr(loop, "run_in_executor", submit)
        return pool, original

    yield install, releases
    for release in releases:
        release.set()
    for pool in pools:
        pool.shutdown(wait=True, cancel_futures=True)


@pytest.mark.asyncio
async def test_cancelled_search_queue_retains_at_most_two_inputs(routing, executor):
    install, releases = executor
    pool, original = install(1)
    started, release = Event(), Event()
    releases.append(release)

    def occupy():
        started.set()
        assert release.wait(3)

    blocker = pool.submit(occupy)
    routing.source = "x" * 1_000_000 + "\nneedle"
    tasks = []
    try:
        await until(started.is_set)
        tasks = [
            asyncio.create_task(search.find_in_file(None, "Assets/Test.cs", "needle"))
            for _ in range(12)
        ]
        await until(lambda: routing.reads == 12)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        tasks.clear()
        task = None
        await asyncio.sleep(0)
        gc.collect()
        assert pool.submissions == 3, "Only two regex jobs may enter the blocked executor"
        assert sum(ref() is not None for ref in routing.refs) == 2
        # Cancelled queued work still owns admission until its worker actually exits.
        refused = await search.find_in_file(None, "Assets/Test.cs", "needle")
        assert refused["success"] is False and "busy" in refused["message"]
    finally:
        release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.wrap_future(blocker)
        await original(pool, lambda: None)
        await asyncio.sleep(0)
    gc.collect()
    assert not any(ref() is not None for ref in routing.refs)
    assert (await search.find_in_file(None, "Assets/Test.cs", "needle"))["success"] is True


@pytest.mark.asyncio
async def test_running_search_and_edit_keep_shared_admission_until_exit(
    routing, executor, monkeypatch
):
    install, releases = executor
    pool, original_executor = install(2)
    release = Event()
    releases.append(release)
    entered = [Event(), Event()]
    exited = [Event(), Event()]
    actual = bounded_regex.find_matches
    errors = []
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: errors.append(context.get("message")))

    def blocked(*args, **kwargs):
        index = 0 if args[0] == "needle" else 1
        entered[index].set()
        try:
            assert release.wait(3)
            return actual(*args, **kwargs)
        finally:
            exited[index].set()

    monkeypatch.setattr(bounded_regex, "find_matches", blocked)
    refused_task = None
    tasks = [
        asyncio.create_task(search.find_in_file(None, "Assets/Test.cs", "needle")),
        asyncio.create_task(
            selection._apply_edits_locally(
                "class C {\n marker;\n}",
                [{"op": "anchor_insert", "anchor": "marker", "text": "new"}],
            )
        ),
    ]
    try:
        await until(lambda: all(event.is_set() for event in entered))
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        tasks.clear()
        task = None
        refused_task = asyncio.create_task(search.find_in_file(None, "Assets/Test.cs", "needle"))
        await until(lambda: refused_task.done() or pool.submissions > 2)
        assert refused_task.done(), "Busy searches must reject without entering the executor queue"
        refused = await refused_task
        assert refused["success"] is False and "busy" in refused["message"]
        assert pool.submissions == 2
    finally:
        release.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if refused_task is not None:
            refused_task.cancel()
            await asyncio.gather(refused_task, return_exceptions=True)
        await until(lambda: all(event.is_set() for event in exited))
        await original_executor(pool, lambda: None)
        await asyncio.sleep(0.01)
        loop.set_exception_handler(previous)
    gc.collect()
    assert not any(ref() is not None for ref in routing.refs)
    assert not errors
    monkeypatch.setattr(bounded_regex, "find_matches", actual)
    assert (await search.find_in_file(None, "Assets/Test.cs", "needle"))["success"] is True
    assert await selection._apply_edits_locally("x", [{"op": "append", "text": "y"}]) == "xy"


@pytest.mark.asyncio
async def test_submission_failure_releases_search_admission(routing, monkeypatch):
    loop = asyncio.get_running_loop()
    original = loop.run_in_executor

    def failed(*_args):
        raise RuntimeError("executor closed")

    with monkeypatch.context() as patched:
        patched.setattr(loop, "run_in_executor", failed)
        with pytest.raises(RuntimeError, match="executor closed"):
            await search.find_in_file(None, "Assets/Test.cs", "needle")
    assert (await search.find_in_file(None, "Assets/Test.cs", "needle"))["success"] is True
    assert loop.run_in_executor == original


@pytest.mark.asyncio
async def test_queue_wait_is_part_of_the_search_budget(routing, executor, monkeypatch):
    install, releases = executor
    pool, original = install(1)
    started, release = Event(), Event()
    releases.append(release)
    clock = [100.0]
    monkeypatch.setattr(bounded_regex, "monotonic", lambda: clock[0])

    def occupy():
        started.set()
        assert release.wait(3)

    blocker = pool.submit(occupy)
    await until(started.is_set)
    task = asyncio.create_task(search.find_in_file(None, "Assets/Test.cs", "needle"))
    try:
        await until(lambda: pool.submissions == 2)
        clock[0] = 102.0
        release.set()
        await asyncio.wrap_future(blocker)
        result = await task
        assert result["success"] is False and "total work" in result["message"]
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await original(pool, lambda: None)
    assert (await search.find_in_file(None, "Assets/Test.cs", "needle"))["success"] is True


@pytest.mark.asyncio
async def test_search_preserves_to_thread_context_without_changing_edit_context(
    routing, monkeypatch
):
    marker = ContextVar("regex-search-context", default="default")
    actual = bounded_regex.find_matches
    observed = []

    def inspect(*args, **kwargs):
        observed.append(marker.get())
        return actual(*args, **kwargs)

    monkeypatch.setattr(bounded_regex, "find_matches", inspect)
    token = marker.set("request")
    try:
        assert (await search.find_in_file(None, "Assets/Test.cs", "needle"))["success"] is True
        await selection._apply_edits_locally(
            "marker", [{"op": "anchor_insert", "anchor": "marker", "text": "new"}]
        )
    finally:
        marker.reset(token)
    assert observed == ["request", "default"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "encoded,pattern,case",
    [(False, "needle", True), (True, "needle", False), (False, "(?r)needle", True)],
)
async def test_matches_keep_order_counts_excerpts_and_encoded_read(routing, encoded, pattern, case):
    routing.encoded = encoded
    result = await search.find_in_file(
        None, "Assets/Test.cs", pattern, max_results=1, ignore_case=case
    )
    assert result["success"] is True
    data = result["data"]
    assert data["count"] == 1 and data["total_matches"] == (2 if case else 1)
    assert data["matches"][0] == {
        "line": 2,
        "content": "needle NEEDLE",
        "match": "NEEDLE" if pattern.startswith("(?r)") else "needle",
        "start": 13 if pattern.startswith("(?r)") else 6,
        "end": 19 if pattern.startswith("(?r)") else 12,
    }
    assert routing.reads == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_sdk_reports_busy_and_recovers_with_the_same_search_result(routing, mode):
    entered = [Event(), Event()]
    release = Event()

    def hold(index):
        entered[index].set()
        assert release.wait(3)

    tasks = [
        asyncio.create_task(
            selection._run_regex_work(bounded_regex.WorkBudget(), lambda i=i: hold(i))
        )
        for i in range(2)
    ]
    app = FastMCP("bounded-file-search")
    app.tool(search.find_in_file)
    try:
        await until(lambda: all(event.is_set() for event in entered))
        async with Client(app, mode=mode) as client:
            result = await client.call_tool(
                "find_in_file", {"uri": "Assets/Test.cs", "pattern": "needle"}
            )
            refused = json.loads(result.content[0].text)
            assert refused["success"] is False and "busy" in refused["message"]
            release.set()
            await asyncio.gather(*tasks)
            result = await client.call_tool(
                "find_in_file", {"uri": "Assets/Test.cs", "pattern": "needle"}
            )
            matched = json.loads(result.content[0].text)
            assert matched["success"] is True and matched["data"]["count"] == 2
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert routing.reads == 2
    gc.collect()
    assert not any(ref() is not None for ref in routing.refs)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["value", "error", "timeout", "cancelled"])
async def test_already_completed_worker_preserves_result_and_releases_once(monkeypatch, outcome):
    loop = asyncio.get_running_loop()

    def immediate(_executor, run):
        future = loop.create_future()
        try:
            future.set_result(run())
        except (ValueError, TimeoutError, asyncio.CancelledError) as exc:
            future.set_exception(exc)
        return future

    def work():
        if outcome == "error":
            raise ValueError("worker rejected")
        if outcome == "timeout":
            raise TimeoutError("worker timed out")
        if outcome == "cancelled":
            raise asyncio.CancelledError
        return "ready"

    monkeypatch.setattr(loop, "run_in_executor", immediate)
    budget = bounded_regex.WorkBudget()
    if outcome == "value":
        assert await selection._run_regex_work(budget, work) == "ready"
    else:
        expected = {
            "error": ValueError,
            "timeout": TimeoutError,
            "cancelled": asyncio.CancelledError,
        }[outcome]
        with pytest.raises(expected):
            await selection._run_regex_work(budget, work)
    assert await selection._run_regex_work(bounded_regex.WorkBudget(), lambda: "reused") == "reused"
